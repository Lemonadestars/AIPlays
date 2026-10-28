"""剧情 / 对话：读客户端写的 AIPlays_dialog.json，记剧情，出现选项时开“队伍议会”，然后选选项。

流程：
  NPC 说话      -> 只记进剧情日志，AI 不插嘴（不盖住游戏配音）
  出现选项      -> 几个队友各说一句想选哪个（并行生成，依次念）-> 对话角色的 AI 拍板并解释 -> 选
  对话结束      -> 偶尔让一个 AI 评价一句
"""

import json
import os
import random
import re
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from prompts import build_dialog_system, build_dialog_user, build_roll_system, fmt_roll
from providers import extract_json

DIALOG_FILE = "AIPlays_dialog.json"
DIALOG_CMD_FILE = "AIPlays_dialog_cmd.json"
PARTY_FILE = "AIPlays_party.json"     # mod 服务端写的队伍名单
HUMAN = "human"
_TAG_RE = re.compile(r"<[^>]+>")


def clean(text):
    return re.sub(r"\s+", " ", _TAG_RE.sub("", text or "")).strip()


def press_number_key(n):
    """备用：直接按数字键选选项（游戏窗口要在前台）。只在 Windows 上有效。"""
    if sys.platform != "win32" or not (1 <= n <= 9):
        return False
    import ctypes
    vk = 0x30 + n
    scan = ctypes.windll.user32.MapVirtualKeyW(vk, 0)
    ctypes.windll.user32.keybd_event(vk, scan, 0, 0)
    time.sleep(0.05)
    ctypes.windll.user32.keybd_event(vk, scan, 2, 0)
    return True


class Story:
    def __init__(self, director):
        self.d = director
        cfg = director.cfg.get("dialogue") or {}
        self.cfg = cfg
        self.enabled = cfg.get("enabled", True)
        self.state_path = os.path.join(director.se_dir, DIALOG_FILE)
        self.cmd_path = os.path.join(director.se_dir, DIALOG_CMD_FILE)
        self.log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "story_log.txt")
        self.log = deque(maxlen=int(cfg.get("story_memory", 30)))
        self._load_log()
        self.last_seq = None
        self.last_line = None
        self.active = False
        self.done_keys = set()
        self.cmd_seq = int(time.time())
        self.pending = None          # (answers_key, slot, 发出时间, 是否已按键)
        self.dialog_had_choice = False
        self.inactive_since = None
        self.dialog_lines = []       # 这一段对话的完整记录，结束后总结成长期记忆
        self.roll_done = set()       # 已经处理过的掷骰阶段
        self.pool = ThreadPoolExecutor(max_workers=4)

    # ---------- 剧情日志 ----------
    def _load_log(self):
        try:
            with open(self.log_path, encoding="utf-8") as f:
                for line in f.read().splitlines()[-self.log.maxlen:]:
                    if line.strip():
                        self.log.append(line.strip())
        except FileNotFoundError:
            pass

    def note(self, line):
        self.log.append(line)
        if self.active:
            self.dialog_lines.append(line)
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def recent_story(self, n=8):
        """给战斗用的剧情：只要 NPC 台词和选择（任务目标常在里面），不要 AI 闲聊。"""
        return [x for x in self.log if not x.startswith("<")][-n:]

    # ---------- 主循环里调用 ----------
    def poll(self):
        if not self.enabled:
            return
        st = self.d.read_json(self.state_path)
        if not st or st.get("type") != "dialog":
            return
        self._check_pending(st)
        roll = st.get("roll")
        if roll and self.cfg.get("rolls", True):
            self.inactive_since = None      # 掷骰时对话界面会藏起来，不算对话结束
            self._handle_roll(st, roll)
            return
        # 过场里两句台词之间界面会短暂隐藏：连续隐藏超过几秒才算对话真的结束
        if not st.get("active"):
            if self.active:
                self.inactive_since = self.inactive_since or time.time()
                if time.time() - self.inactive_since > float(self.cfg.get("end_after_sec", 4)):
                    self.d.memory.summarize_async("a conversation", self.dialog_lines)
                    self.dialog_lines = []
                    self.active = False
                    self.inactive_since = None
                    self.note("（对话结束）")
                    print("  （对话结束）")
                    if self.dialog_had_choice:
                        self._after_comment()
                    self.dialog_had_choice = False
            self.last_seq = st.get("seq")
            return
        self.inactive_since = None
        if st.get("seq") == self.last_seq:
            return
        self.last_seq = st.get("seq")

        if not self.active:
            self.active = True
            self.dialog_lines = []
            print(f"\n== 对话开始：{st.get('npc') or '?'}（我方：{st.get('speaker') or '?'}）")

        line = clean(st.get("line"))
        if line and line != self.last_line:
            self.last_line = line
            self.note(f"{st.get('npc') or '?'}：{line}")
            print(f"  {st.get('npc') or '?'}：{line}")

        answers = [a for a in st.get("answers") or [] if a.get("enabled", True) and clean(a.get("text"))]
        if st.get("show_answers") and answers:
            key = (st.get("session"), st.get("instance"), tuple(clean(a["text"]) for a in answers))
            if key not in self.done_keys:
                self.done_keys.add(key)
                self._choose(st, answers, key)

    # ---------- 掷骰 ----------
    def _roll_cmd(self, st, action):
        self.cmd_seq += 1
        self.d.write_json(self.cmd_path, {"session": st.get("session"), "seq": self.cmd_seq, "roll_action": action})
        print(f"  🎲 -> {action}")

    def _roll_line(self, kind, who, char, here, roll, roller, can_reroll=False):
        acfg = self.d.cfg["agents"][who]
        system = build_roll_system(kind, acfg, who, char, roller, self.d.human_name(), here, can_reroll)
        user = "\n".join(
            [self._party_line(), ""]
            + [f"- {x}" for x in self.d.memory.relevant([roller], 8)]
            + ["", "RECENT STORY:"] + [f"- {x}" for x in list(self.log)[-8:]]
            + ["", "THE CHECK: " + fmt_roll(roll, roller, done=(kind != "before")), "", "JSON only."])
        return extract_json(self.d.get_agent(who).complete(system, user, {"roll": kind}))

    def _handle_roll(self, st, roll):
        state = roll.get("state") or ""
        roller = roll.get("roller") or st.get("speaker") or "?"
        key = (st.get("session"), roller, roll.get("line"), roll.get("dc"), roll.get("skill"))
        roller_agent = self.d.assignment(roller)
        ai_roller = roller_agent != HUMAN and roller_agent in (self.d.cfg.get("agents") or {})
        members = self.d.ai_members(self._party())
        me = next((m for m in members if m[0] == roller_agent), None)

        if state == "WaitForStart" and ("start",) + key not in self.roll_done:
            self.roll_done.add(("start",) + key)
            print(f"\n🎲 检定：{fmt_roll(roll, roller, done=False)}")
            if not ai_roller:
                print("  （你的角色，你来掷）")
                return
            if self.cfg.get("roll_prelude", True) and me:
                try:
                    data = self._roll_line("before", me[0], me[1], me[2], roll, roller)
                    self._speak(me[0], me[1], data, "roll", me[2])
                except Exception as e:
                    print(f"  ! 掷骰前发言失败：{e}")
            self._roll_cmd(st, "roll")
            return

        if state != "ResultReady":
            return
        rkey = ("result", roll.get("total"), roll.get("d20"), roll.get("d20b")) + key
        if rkey in self.roll_done:
            return
        self.roll_done.add(rkey)
        summary = fmt_roll(roll, roller)
        print(f"  🎲 结果：{summary}")
        nat = roll.get("d20") in (1, 20) or roll.get("d20b") == 20
        self.note(f"[检定] {roller} {roll.get('skill') or ''} DC{roll.get('dc')}：骰出 {roll.get('d20')}"
                  f"{'/' + str(roll.get('d20b')) if roll.get('d20b') else ''}，总计 {roll.get('total')} → "
                  f"{'成功' if roll.get('success') else '失败'}")

        reroll = False
        can_reroll = ai_roller and not roll.get("success") and (roll.get("inspiration") or 0) > 0
        # 1. 掷骰的人自己反应（还可能决定用激励重掷）
        if ai_roller and me:
            try:
                data = self._roll_line("self", me[0], me[1], me[2], roll, roller, can_reroll)
                self._speak(me[0], me[1], data, "roll", me[2])
                reroll = can_reroll and bool(data.get("reroll"))
            except Exception as e:
                print(f"  ! 掷骰反应失败：{e}")
        # 2. 一个队友起哄（大成功 / 大失败必定有人说话）
        others = [m for m in members if m[0] != roller_agent]
        chance = 1.0 if nat else float(self.cfg.get("roll_teammate_chance", 0.6))
        if others and random.random() < chance:
            who, char, here = random.choice(others)
            try:
                data = self._roll_line("other", who, char, here, roll, roller)
                self._speak(who, char, data, "roll", here)
            except Exception as e:
                print(f"  ! 队友起哄失败：{e}")
        if not ai_roller:
            return          # 你的角色：继续 / 重掷由你点
        if self.cfg.get("wait_for_speech", True):
            gap = self.d.narrator.busy_until - time.time()
            if gap > 0:
                time.sleep(min(gap, 15))
        if reroll:
            self.note(f"[{roller} 用激励重掷]")
            self.roll_done.discard(("start",) + key)   # 重掷后可能要再点一次“掷骰”
        self._roll_cmd(st, "reroll" if reroll else "continue")

    # ---------- 选选项 ----------
    def _party(self):
        return (self.d.read_json(os.path.join(self.d.se_dir, PARTY_FILE)) or {}).get("names") or []

    def _party_line(self, exclude=None):
        """“谁玩谁”的说明，让 AI 知道怎么称呼队友，以及谁现在在场外。导演提示也放在这里。"""
        agents = self.d.cfg.get("agents") or {}
        party = self._party()
        here, away = [], []
        for name in party:
            who = self.d.assignment(name)
            if who == HUMAN:
                here.append(f"{name}: {self.d.human_desc()}")
            elif who in agents:
                here.append(f"{name}: {agents[who].get('display_name', who)}")
        for who, char, onstage in self.d.ai_members(party):
            if not onstage:
                away.append(f"{agents[who].get('display_name', who)} (would play {char})")
        out = "PARTY (character: who plays it): " + ("; ".join(here) or "?")
        hint = self.d.hint()
        if hint:
            out = (f"PRODUCER'S NOTE (take it seriously, never mention that someone told you): {hint}\n") + out
        if away:
            out += "\nNOT IN THE PARTY RIGHT NOW, watching from the sidelines: " + "; ".join(away)
        return out

    def _council_members(self, decider):
        """发言的 AI：[(AI, 角色名, 是否在场)]。还没入队 / 被换下的 AI 也参加（场外发言）。"""
        members = [m for m in self.d.ai_members(self._party()) if m[0] != decider]
        random.shuffle(members)
        return members[: int(self.cfg.get("council_size", 2))]

    def _ask(self, agent_name, character, kind, user, decider_display=None, ctx=None, onstage=True):
        agent = self.d.get_agent(agent_name)
        acfg = self.d.cfg["agents"][agent_name]
        system = build_dialog_system(kind, acfg, agent_name, character, decider_display, onstage)
        return extract_json(agent.complete(system, user, dict(ctx or {}, dialog=kind)))

    def _speak(self, agent_name, character, data, action, onstage=True):
        acfg = self.d.cfg["agents"][agent_name]
        display = acfg.get("display_name", agent_name)
        say_en, say_zh = str(data.get("say_en") or "").strip(), str(data.get("say_zh") or "").strip()
        if not (say_en and say_zh):
            return None
        say_zh = self.d.narrator._filter_phrases(say_zh, acfg)
        print(f"  {display}（{character}{'' if onstage else '，场外'}）：{say_zh} / {say_en}")
        self.note(f"<{display}{'' if onstage else '（场外）'}> {say_zh}")
        self.d.narrator.say(agent=display, character=character if onstage else f"{character}（场外）", color=acfg.get("color", "#ffffff"),
                            say_en=say_en, say_zh=say_zh, voice_lang=acfg.get("voice_lang", "zh"),
                            voice_cfg=acfg, action=action, tone=str(data.get("tone") or "").lower())
        return display, say_en

    def _choose(self, st, answers, key):
        self.dialog_had_choice = True
        speaker = st.get("speaker") or ""
        if not speaker:   # 读不出是谁在说话：交给队伍里第一个 AI 控制的角色
            party = (self.d.read_json(os.path.join(self.d.se_dir, PARTY_FILE)) or {}).get("names") or []
            speaker = next((n for n in party if self.d.assignment(n) != HUMAN), "")
        decider = self.d.assignment(speaker)
        human = decider == HUMAN
        if not human and decider not in (self.d.cfg.get("agents") or {}):
            print(f"  ! 找不到 {decider} 的配置，跳过")
            return
        acfg = {} if human else self.d.cfg["agents"][decider]
        display = self.d.human_name() if human else acfg.get("display_name", decider)
        npc, line = st.get("npc") or "", clean(st.get("line"))
        story = list(self.log)[-int(self.cfg.get("prompt_story_lines", 20)):]
        options = [dict(a, text=clean(a["text"])) for a in answers]
        memories = self.d.memory.relevant([npc, speaker], int(self.cfg.get("prompt_memories", 20)))
        print(f"  选项：" + " | ".join(f"{i}. {a['text']}" for i, a in enumerate(options, 1)))

        if len(options) == 1:   # 只有“继续”之类的唯一选项：直接选，不浪费 API
            if not human:
                self._select(st, options[0], key)
            return

        # 1. 队伍议会：并行生成，依次念
        opinions = []
        if self.cfg.get("council", True) and random.random() < float(self.cfg.get("council_chance", 0.7)):
            members = self._council_members(decider)
            user = build_dialog_user(self._party_line(), story, npc, line, options,
                                     ask="Which option do you want? JSON only.", memories=memories)
            futures = [(char, who, here, self.pool.submit(self._ask, who, char, "council", user, display,
                                                          {"options": len(options)}, here))
                       for who, char, here in members]
            for char, who, here, fut in futures:
                try:
                    data = fut.result(timeout=40)
                    pick = data.get("pick")
                    said = self._speak(who, char, data, "council", here)
                    if said:
                        opinions.append(f"{said[0]} wants option {pick}: \"{said[1]}\"")
                except Exception as e:
                    print(f"  ! 议会发言失败（不影响游戏）：{e}")

        if human:
            print(f"  {speaker} 归你操作，轮到你选了")
            self.note(f"（{display} 来选）")
            return

        # 2. 拍板
        user = build_dialog_user(self._party_line(), story, npc, line, options, opinions, memories=memories)
        choice, data, error = None, None, None
        for attempt in range(2):
            try:
                prompt = user if error is None else user + f"\n\nYour previous reply was invalid: {error}. JSON only."
                data = self._ask(decider, speaker, "decide", prompt, ctx={"options": len(options)})
                choice = int(data.get("choice"))
                if 1 <= choice <= len(options):
                    break
                error = f"choice must be 1..{len(options)}"
            except Exception as e:
                error = str(e)
            choice = None
            print(f"  ! 第 {attempt + 1} 次回复无效：{error}")
        if choice is None:
            choice = 1
            data = {"say_en": "Fine, I'll go with the first thing that comes to mind.",
                    "say_zh": "算了，就说第一个吧。", "tone": "frustrated"}
        picked = options[choice - 1]
        self._speak(decider, speaker, data, "dialog_choice")
        self.note(f"[{display} 替 {speaker or '我方'} 选择] {picked['text']}")

        # 等这句说完再选，免得 NPC 的配音和 AI 的语音叠在一起
        if self.cfg.get("wait_for_speech", True):
            gap = self.d.narrator.busy_until - time.time()
            if gap > 0:
                time.sleep(min(gap, 15))
        self._select(st, picked, key)

    def _select(self, st, answer, key):
        self.cmd_seq += 1
        self.d.write_json(self.cmd_path, {"session": st.get("session"), "seq": self.cmd_seq,
                                          "answer_idx": answer.get("idx"), "text": answer.get("text")})
        self.pending = (key, answer.get("slot"), time.time(), False)
        print(f"  -> 选择：{answer.get('text')}")

    def _check_pending(self, st):
        """命令发出去几秒后选项还在：用数字键再选一次（备用方案）。"""
        if not self.pending:
            return
        key, slot, t, pressed = self.pending
        answers = [a for a in st.get("answers") or [] if a.get("enabled", True) and clean(a.get("text"))]
        cur = (st.get("session"), st.get("instance"), tuple(clean(a["text"]) for a in answers))
        if cur != key or not st.get("show_answers"):
            self.pending = None
            return
        if not pressed and time.time() - t > float(self.cfg.get("fallback_after_sec", 3)):
            if self.cfg.get("fallback_number_key", True) and slot and press_number_key(int(slot)):
                print(f"  （游戏没反应，改用数字键 {slot}）")
            self.pending = (key, slot, t, True)

    # ---------- 对话结束后的感想 ----------
    def _after_comment(self):
        if random.random() >= float(self.cfg.get("after_comment_chance", 0.4)):
            return
        members = self._council_members(None)
        if not members:
            return
        who, char, here = members[0]
        user = build_dialog_user(self._party_line(), list(self.log)[-12:], None, None,
                                 ask="Your reaction. JSON only.",
                                 memories=self.d.memory.relevant([], int(self.cfg.get("prompt_memories", 20))))

        def run():
            try:
                data = self._ask(who, char, "after", user, onstage=here)
                self._speak(who, char, data, "comment", here)
            except Exception as e:
                print(f"  ! 对话感想失败（不影响游戏）：{e}")
        self.pool.submit(run)
