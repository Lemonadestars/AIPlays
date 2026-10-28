"""AIPlays 调度器：读取游戏状态 -> 交给对应的 AI -> 解说 -> 把指令写回游戏。

用法：
    python director.py                     # 用 config.yaml
    python director.py --config my.yaml
    python director.py --se-dir 某个目录    # 测试时配合 fake_game.py 使用
"""

import argparse
import json
import math
import os
import sys
import threading
import time
from collections import deque

import yaml

from banter import Banter
from narrator import Narrator
from prompts import build_system, build_user
from levelup import LevelUp
from memory import Memory
from setup_env import apply_key_checks, load_config, load_keys
from story import Story
from providers import extract_json, make_agent

STATE_FILE = "AIPlays_state.json"
CMD_FILE = "AIPlays_command.json"
ROSTER_FILE = "AIPlays_roster.json"   # 告诉 mod 哪些角色归人类操作
HUMAN = "human"
VALID_ACTIONS = {"use_spell", "move", "use_object", "end_turn"}


def default_se_dir():
    base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
    return os.path.join(base, "Larian Studios", "Baldur's Gate 3", "Script Extender")


def read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return None  # 文件不存在，或者游戏正在写，下一轮再读


def write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


class InvalidDecision(Exception):
    pass


def build_view(state):
    """给单位编号（E1/A1/N1），AI 只看编号，不碰 GUID，避免编造目标。"""
    counters = {"E": 0, "A": 0, "N": 0}
    units = []
    for u in state.get("units", []):
        prefix = "E" if u.get("enemy") else ("A" if u.get("ally") else "N")
        counters[prefix] += 1
        units.append(dict(u, label=f"{prefix}{counters[prefix]}"))
    objects = [dict(o, label=f"O{i}") for i, o in enumerate(state.get("objects") or [], 1)]   # 先编号，过滤后重编
    return {
        "actor": state["actor"],
        "units": units,
        "objects": objects,
        "actions_taken": state.get("actions_taken", 0),
        "last_result": state.get("last_result"),
    }


def point_toward(src, dst, stop_short=1.2):
    """从 src 走向 dst，停在离目标 stop_short 米的位置（直接走到目标脚下会寻路失败）。"""
    dx, dy, dz = (dst[i] - src[i] for i in range(3))
    dist = math.sqrt(dx * dx + dz * dz)
    if dist <= stop_short or dist == 0:
        return list(src)
    r = (dist - stop_short) / dist
    return [round(src[0] + dx * r, 2), round(src[1] + dy * r, 2), round(src[2] + dz * r, 2)]


def validate(decision, view):
    """检查 AI 的决定是否合法，并把编号翻译成游戏能用的 GUID / 坐标。"""
    if not isinstance(decision, dict):
        raise InvalidDecision("reply must be a JSON object")
    action = str(decision.get("action", "")).strip()
    if action not in VALID_ACTIONS:
        raise InvalidDecision(f'"action" must be one of {sorted(VALID_ACTIONS)}')

    say_en = str(decision.get("say_en") or "").strip()
    say_zh = str(decision.get("say_zh") or "").strip()
    if not say_en or not say_zh:
        raise InvalidDecision('both "say_en" and "say_zh" are required')

    actor = view["actor"]
    by_label = {u["label"].upper(): u for u in view["units"]}
    objects = {o["label"].upper(): o for o in view.get("objects") or []}
    cmd = {"action": action}
    target_name = None

    if action == "use_object" or (action == "move" and str(decision.get("target", "")).strip().upper() in objects):
        label = str(decision.get("target", "")).strip().upper()
        obj = objects.get(label)
        if obj is None:
            raise InvalidDecision(f'unknown object "{label}". Use one of: ' + (", ".join(objects) or "(no objects nearby)"))
        target_name = obj["name"]
        cmd["target"] = obj["guid"]
        if obj.get("pos") and actor.get("pos"):
            cmd["position"] = point_toward(actor["pos"], obj["pos"], stop_short=1.0)
        if action == "move" and "position" not in cmd:
            raise InvalidDecision("that object has no known position")
    elif action in ("use_spell", "move"):
        label = str(decision.get("target", "")).strip().upper()
        if label == "SELF":
            target_guid, target_unit = actor["guid"], None
            target_name = actor["name"]
        elif label in by_label:
            target_unit = by_label[label]
            target_guid, target_name = target_unit["guid"], target_unit["name"]
        else:
            raise InvalidDecision(f'unknown target "{label}". Use one of: SELF, ' + ", ".join(by_label))

        if action == "use_spell":
            spells = actor.get("spells") or []
            spell = str(decision.get("spell", "")).strip()
            match = next((s for s in spells if s.lower() == spell.lower()), None)
            if not match:
                raise InvalidDecision(f'spell "{spell}" is not in your available list')
            # 够不着：射程 + 剩余移动力都不够，游戏会直接不放，提前拒绝
            rng = (actor.get("spell_ranges") or {}).get(match)
            dist = target_unit.get("dist") if target_unit else None
            move_left = ((actor.get("resources") or {}).get("movement") or 0)
            if rng is not None and dist is not None and dist > rng + move_left + 0.5:
                raise InvalidDecision(
                    f"{target_name} is {dist}m away but {match} reaches {rng}m and you only have {move_left}m of "
                    f"movement left — out of reach. Pick a target in reach, a ranged option, or move/end turn.")
            cmd.update(spell=match, target=target_guid)
        else:
            if target_unit is None or not target_unit.get("pos") or not actor.get("pos"):
                raise InvalidDecision("move needs a target unit with a known position")
            cmd["position"] = point_toward(actor["pos"], target_unit["pos"])

    tone = str(decision.get("tone") or "").strip().lower()
    return cmd, {"say_en": say_en, "say_zh": say_zh, "target_name": target_name,
                 "spell": cmd.get("spell"), "tone": tone}


class Director:
    def __init__(self, cfg, se_dir):
        self.cfg = cfg
        self.se_dir = se_dir
        self.state_path = os.path.join(se_dir, STATE_FILE)
        self.cmd_path = os.path.join(se_dir, CMD_FILE)
        self.agents = {}
        self.recent = deque(maxlen=int(cfg.get("recent_events", 10)))
        self.narrator = Narrator(cfg.get("narration", {}), os.path.dirname(os.path.abspath(__file__)))
        self.last_key = None
        self.memory_key = None
        self.banter = Banter(self)
        self.read_json = read_json
        self.write_json = write_json_atomic
        self.memory = Memory(self)
        self.fight_log = []           # 这一场战斗的完整记录，打完总结成长期记忆
        self.log_seen = self.combat_log_now() if os.path.isdir(se_dir) else 0   # 战斗日志读到哪了（旧的不算）
        self.log_window = deque(maxlen=6)
        self.last_spoken_action = None
        self.story = Story(self)
        self.levelup = LevelUp(self)
        # 解说排队在后台线程里念：指令马上发给游戏，不用等上一句说完、也不用等语音合成
        self.speech_async = (cfg.get("narration") or {}).get("speak_async", True)
        self.speech_queue = deque()
        self.speech_cv = threading.Condition()
        threading.Thread(target=self._speech_worker, daemon=True).start()

    def _speech_worker(self):
        while True:
            with self.speech_cv:
                while not self.speech_queue:
                    self.speech_cv.wait()
                kwargs = self.speech_queue.popleft()
                self.speaking = True
            try:
                self.narrator.say(**kwargs)
            except Exception as e:
                print(f"  ! 解说失败：{e}")
            finally:
                self.speaking = False

    def paused(self):
        """导演提示窗口里勾了“暂停 AI”：director 文件夹里有 paused.txt 且内容是 1。"""
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "paused.txt"), encoding="utf-8") as f:
                return f.read().strip() == "1"
        except FileNotFoundError:
            return False

    def heartbeat(self, beat):
        """每秒告诉 mod “调度器还开着”。mod 发现心跳停了（窗口关了）或暂停了，就把角色交还给玩家。"""
        p = self.paused()
        if p != getattr(self, "_was_paused", False):
            self._was_paused = p
            print("\n  ⏸ AI 已暂停：所有角色交给你操作" if p else "\n  ▶ AI 已恢复")
        try:
            write_json_atomic(os.path.join(self.se_dir, "AIPlays_director.json"), {"beat": beat, "paused": p})
        except OSError:
            pass

    def wait_speech_done(self, limit=25):
        """等解说队列清空、最后一句播完（最多 limit 秒）。"""
        t_end = time.time() + limit
        while time.time() < t_end:
            with self.speech_cv:
                pending = len(self.speech_queue) + (1 if getattr(self, "speaking", False) else 0)
            if pending == 0 and self.narrator.busy_until - time.time() <= 0 and not self.narrator.lock.locked():
                return
            time.sleep(0.2)

    def speak(self, **kwargs):
        """战斗解说：异步排队。积压太多（AI 比嘴快）时丢掉最旧的一句，免得语音越来越落后于画面。"""
        if not self.speech_async:
            return self.narrator.say(**kwargs)
        with self.speech_cv:
            while len(self.speech_queue) >= 2:
                old = self.speech_queue.popleft()
                print(f"  （解说积压，跳过：{old.get('say_zh')}）")
            self.speech_queue.append(kwargs)
            self.speech_cv.notify()
        return 0

    def ai_members(self, onstage_names=()):
        """四个 AI 的名单：[(AI, 角色名, 是否在场)]。角色还没入队 / 被换下时也在名单里（场外发言）。"""
        agents = self.cfg.get("agents") or {}
        mapping = [(str(c), str(w)) for c, w in (self.cfg.get("characters") or {}).items()]
        out, seen = [], set()
        for _, who in mapping:
            if who in seen or who == HUMAN or who not in agents:
                continue
            seen.add(who)
            here = next((n for n in onstage_names if self.assignment(n) == who), None)
            if here:
                out.append((who, here, True))
            else:
                out.append((who, next(c for c, w in mapping if w == who), False))
        return out

    def hint(self):
        """导演提示：director 文件夹里 hint.txt 的内容（你随时可以改，下一步就生效；清空就没有）。"""
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "hint.txt"), encoding="utf-8") as f:
                return " ".join(x.strip() for x in f.read().splitlines()
                                if x.strip() and not x.strip().startswith("#"))
        except FileNotFoundError:
            return ""

    def add_landmarks(self, view, state):
        """近处物体去掉装饰品（椅子、灯……）只留几个；远处的物体里，名字在剧情里出现过（比如“魔舵”）
        或者含有目标关键词的，也给 AI 看（标成远处的目标）。"""
        junk = [str(w) for w in (self.cfg.get("ignore_objects") or []) if w]
        keep = int(self.cfg.get("max_objects", 8))
        objs = [o for o in view.get("objects") or [] if not any(j in (o.get("name") or "") for j in junk)]
        seen_names, near_list = set(), []
        for o in objs:                       # 同名的只留最近的一个
            if o["name"] not in seen_names:
                seen_names.add(o["name"])
                near_list.append(o)
        view["objects"] = [dict(o, label=f"O{i}") for i, o in enumerate(near_list[:keep], 1)]
        near = {o["guid"] for o in view["objects"]}
        text = "\n".join(list(self.story.log) + self.memory.relevant([], 30))
        words = [str(w) for w in (self.cfg.get("objective_words") or []) if w]
        picked = []
        for o in state.get("landmarks") or []:
            name = o.get("name") or ""
            if o.get("guid") in near or len(name) < 2 or name in {x["name"] for x in view["objects"]}:
                continue
            if any(j in name for j in junk):
                continue
            if name in text or any(w in name for w in words):
                picked.append(o)
            if len(picked) >= 4:
                break
        n = len(view.get("objects") or [])
        for i, o in enumerate(picked, n + 1):
            view.setdefault("objects", []).append(dict(o, label=f"O{i}", far=True))

    def combat_log(self):
        """游戏自己的战斗日志（mod 客户端导出）：[(时间, 文字)]，旧的在前。"""
        data = read_json(os.path.join(self.se_dir, "AIPlays_combatlog.json")) or {}
        if data.get("session") != getattr(self, "_log_session", None):
            # 读档 / 重开游戏后，游戏时间从头算，旧的进度作废
            if getattr(self, "_log_session", None) is not None:
                self.log_seen = 0
            self._log_session = data.get("session")
        return [(float(x.get("t") or 0), str(x.get("text") or "")) for x in data.get("lines") or []]

    def combat_log_since(self, t):
        return [text for ts, text in self.combat_log() if ts > (t or 0)]

    def combat_log_now(self):
        log = self.combat_log()
        return log[-1][0] if log else 0

    def human_desc(self):
        """“User（真人玩家，队长）”这样的说明。"""
        role = str(self.cfg.get("human_role") or "").strip()
        return f"{self.human_name()} (the human player" + (f", {role})" if role else ")")

    def human_name(self):
        return str(self.cfg.get("human_name") or "the human player")

    def assignment(self, character_name):
        """按 config.yaml 的 characters 找到这个角色归谁（第一个匹配的名字生效）。"""
        mapping = self.cfg.get("characters") or {}
        name_l = (character_name or "").lower()
        for key, agent_name in mapping.items():
            if str(key).lower() in name_l:
                return str(agent_name)
        return str(self.cfg.get("default_agent", "claude"))

    def write_roster(self):
        """把角色分配写给 mod：归 human 的角色 mod 不会接管。"""
        mapping = self.cfg.get("characters") or {}
        roster = {
            "rules": [[str(k).lower(), str(v) == HUMAN] for k, v in mapping.items()],
            "default_human": str(self.cfg.get("default_agent", "claude")) == HUMAN,
        }
        write_json_atomic(os.path.join(self.se_dir, ROSTER_FILE), roster)
        agents = self.cfg.get("agents") or {}
        for k, v in mapping.items():
            if str(v) != HUMAN and str(v) not in agents:
                print(f"  ! characters 里的 {k}: {v} —— agents 里没有 {v}")
        humans = [str(k) for k, v in mapping.items() if str(v) == HUMAN]
        print("角色分配：" + "，".join(f"{k} → {v}" for k, v in mapping.items())
              + f"；其他角色 → {self.cfg.get('default_agent', 'claude')}")
        if humans:
            print("由你操作：" + "、".join(humans))

    def tag_players(self, view, self_agent=None):
        """给队友标上“由哪个 AI 控制”，让 AI 之间用 Claude / GPT 这样的名字互相称呼。"""
        agents = self.cfg.get("agents") or {}
        for u in view["units"]:
            if not u.get("ally"):
                continue
            who = self.assignment(u.get("name"))
            if who == HUMAN:
                u["played_by"] = self.human_desc()
            elif who == self_agent:
                u["played_by"] = "you (another of your characters)"
            elif who in agents:
                u["played_by"] = agents[who].get("display_name", who)

    def get_agent(self, chosen):
        if chosen not in self.agents:
            acfg = (self.cfg.get("agents") or {}).get(chosen)
            if acfg is None:
                raise RuntimeError(f"config.yaml 里没有名为 {chosen} 的 agent")
            self.agents[chosen] = make_agent(chosen, acfg)
        return self.agents[chosen]

    def agent_for(self, character_name):
        chosen = self.assignment(character_name)
        if chosen == HUMAN:
            return HUMAN, None
        if chosen not in self.agents:
            acfg = (self.cfg.get("agents") or {}).get(chosen)
            if acfg is None:
                raise RuntimeError(f"config.yaml 里没有名为 {chosen} 的 agent")
            self.agents[chosen] = make_agent(chosen, acfg)
        return chosen, self.agents[chosen]

    def already_answered(self, state):
        cmd = read_json(self.cmd_path)
        return bool(cmd) and cmd.get("session") == state.get("session") and cmd.get("seq") == state.get("seq")

    def decide(self, agent, agent_name, view):
        acfg = self.cfg["agents"][agent_name]
        system = build_system(acfg, agent_name, view["actor"]["name"])
        system += f"\n\nAlso in the party: {self.human_desc()}. Call them {self.human_name()}."
        names = [view["actor"]["name"]] + [u["name"] for u in view["units"] if not u.get("dead")]
        new_log = self.combat_log_since(self.log_seen)
        self.log_seen = self.combat_log_now()
        self.fight_log.extend(f"（战斗日志）{x}" for x in new_log)
        self.log_window.extend(new_log)
        hint = self.hint()
        if hint:
            view["hint"] = hint
        user = build_user(view, list(self.recent), self.story.recent_story(),
                          self.memory.relevant(names, int((self.cfg.get("memory") or {}).get("combat_memories", 8))),
                          list(self.log_window))
        ctx = {"units": view["units"], "spells": view["actor"].get("spells") or [],
               "actions_taken": view.get("actions_taken", 0)}

        error = None
        for attempt in range(2):
            prompt = user if error is None else (
                user + f"\n\nYour previous reply was invalid: {error}\nFix it and reply with JSON only.")
            try:
                text = agent.complete(system, prompt, ctx)
                decision = extract_json(text)
                return validate(decision, view)
            except InvalidDecision as e:
                error = str(e)
            except ValueError as e:  # JSON 解析失败
                error = f"could not parse JSON ({e})"
            print(f"  ! 第 {attempt + 1} 次回复无效：{error}")

        # 两次都不合法：结束回合，也当成节目效果念出来
        return {"action": "end_turn"}, {
            "say_en": "I... may have confused myself. Ending my turn.",
            "say_zh": "我好像把自己绕晕了，先结束回合吧。",
            "target_name": None, "spell": None, "tone": "frustrated",
        }

    def handle(self, state):
        # 读档（session 变了）或换了一场战斗：清空“最近发生的事”，免得 AI 以为还在上一场
        memory_key = (state.get("session"), state.get("combat"))
        if memory_key != self.memory_key:
            if self.memory_key is not None:
                print("  （检测到读档或新战斗，已清空战斗记忆）")
                self.memory.summarize_async("a fight", self.fight_log)
            self.fight_log = []
            self.log_window.clear()
            self.recent.clear()
            self.memory_key = memory_key
        view = build_view(state)
        self.add_landmarks(view, state)
        character = view["actor"]["name"]
        self.tag_players(view, self.assignment(character))
        agent_name, agent = self.agent_for(character)
        if agent is None:
            print(f"\n#{state['seq']} {character} 归你操作，跳过（mod 版本太旧？重启游戏载入新版 mod）")
            return
        acfg = self.cfg["agents"][agent_name]
        display = acfg.get("display_name", agent_name)
        print(f"\n#{state['seq']} {display} 控制 {character}（本回合第 {view['actions_taken'] + 1} 个动作）")

        t0 = time.time()
        try:
            cmd, info = self.decide(agent, agent_name, view)
        except Exception as e:  # API 出错、没 key 等：不让游戏卡住
            print(f"  ! 调用 {display} 失败：{e}")
            cmd = {"action": "end_turn"}
            info = {"say_en": "Connection lost. I'll sit this one out.",
                    "say_zh": "掉线了，这回合我先歇着。", "target_name": None, "spell": None}
        think = time.time() - t0
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "think_times.jsonl"), "a",
                      encoding="utf-8") as f:
                f.write(json.dumps({"wall": time.strftime("%H:%M:%S"), "agent": agent_name,
                                    "model": acfg.get("model"), "sec": round(think, 2)}) + "\n")
        except OSError:
            pass
        print(f"  思考 {think:.1f}s -> {cmd['action']} {info.get('spell') or ''} {info.get('target_name') or ''}")
        print(f"  EN: {info['say_en']}\n  ZH: {info['say_zh']}")

        # 连续走路（上一步也是走路、或者被挡住重走）只念第一句，免得一直重复“我走过去”卡节奏
        quiet = cmd["action"] == "move" and self.last_spoken_action == ("move", state.get("session"), character)
        self.last_spoken_action = (cmd["action"], state.get("session"), character)
        if quiet:
            print("  （连续移动，这句不念）")
        wait = 0 if quiet else self.speak(
            agent=display, character=character, color=acfg.get("color", "#ffffff"),
            say_en=info["say_en"], say_zh=info["say_zh"],
            voice_lang=acfg.get("voice_lang", "en"), voice_cfg=acfg,
            action=cmd["action"], spell=info.get("spell"), target=info.get("target_name"),
            tone=info.get("tone"),
        )
        if wait > 0:
            time.sleep(wait)
        # 结束回合前等这个 AI 把话说完，免得下一个 AI 已经开始行动、这边还在念
        if cmd["action"] == "end_turn" and (self.cfg.get("narration") or {}).get("finish_speech_before_end_turn", True):
            self.wait_speech_done()

        cmd.update(session=state["session"], seq=state["seq"])
        write_json_atomic(self.cmd_path, cmd)
        self.banter.record(state, view, agent_name, character, cmd, info)

        summary = {"use_spell": f"used {info.get('spell')} on {info.get('target_name')}",
                   "move": f"moved toward {info.get('target_name')}",
                   "use_object": f"used {info.get('target_name')}",
                   "end_turn": "ended turn"}[cmd["action"]]
        self.recent.append(f"[{display} as {character}] {summary} — \"{info['say_en']}\"")
        self.fight_log.append(f"[{display} as {character}] {summary} — "
                              f"{self.narrator._filter_phrases(info['say_zh'], acfg)}")

    def run(self):
        self.write_roster()
        print(f"AIPlays 调度器已启动，监听：{self.state_path}")
        print(f"剧情监听：{self.story.state_path}（对话{'开启' if self.story.enabled else '关闭'}）")
        print(f"字幕页面：http://localhost:{self.narrator.port}/overlay.html   (Ctrl+C 退出)")
        self._was_paused = False
        # 心跳单独一个线程：AI 想得久（十几秒）时也不会被 mod 当成调度器关了
        def beat_loop():
            n = 0
            while True:
                n += 1
                self.heartbeat(n)
                time.sleep(1)
        threading.Thread(target=beat_loop, daemon=True).start()
        while True:
            state = read_json(self.state_path)
            if state and state.get("type") == "turn" and not self._was_paused:
                key = (state.get("session"), state.get("seq"))
                if key != self.last_key:
                    self.last_key = key
                    self.banter.observe(state)   # 看上一个技能的实际效果，偶尔让队友评价一句
                    if not self.already_answered(state):
                        self.handle(state)
            # 剧情、升级出错只打印，不让调度器整个退出（战斗还得继续）
            for name, fn in (("剧情", self.story.poll), ("升级", self.levelup.poll)):
                if self._was_paused:
                    break      # 暂停时对话选项、升级都交给你
                try:
                    fn()
                except Exception as e:
                    import traceback
                    print(f"  ! {name}模块出错（已跳过）：{e}")
                    traceback.print_exc()
            time.sleep(0.25)


def main():
    parser = argparse.ArgumentParser(description="AIPlays director")
    parser.add_argument("--config", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml"))
    parser.add_argument("--se-dir", default=None, help="覆盖 Script Extender 数据目录")
    args = parser.parse_args()

    keys = load_keys()
    if keys:
        print(f"已从 keys.env 读取 {len(keys)} 个 API key")
    cfg = apply_key_checks(load_config(args.config))
    se_dir = args.se_dir or cfg.get("se_dir") or default_se_dir()
    os.makedirs(se_dir, exist_ok=True)

    try:
        Director(cfg, se_dir).run()
    except KeyboardInterrupt:
        print("\n已退出")
        sys.exit(0)


if __name__ == "__main__":
    main()
