"""队友互评：某个 AI 放完技能后，偶尔让另一个 AI 用自己的人设评价一句。

流程：
  1. 调度器发出 use_spell 指令时，record() 记下目标当时的血量
  2. 游戏导出下一个状态时，observe() 对比血量，算出实际效果（伤害 / 打空 / 击杀 / 治疗）
  3. 按概率和冷却挑一个队友 AI，在后台线程里生成一句评价，排进解说队列
     （生成太慢、已经过时的评价会被丢掉）
"""

import random
import threading

from prompts import build_reaction_system, build_reaction_user
from providers import extract_json


def _key(guid):
    return (guid or "")[-36:]


class Banter:
    def __init__(self, director):
        self.d = director
        cfg = director.cfg.get("interactions") or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.chance = float(cfg.get("chance", 0.3))
        self.cooldown = int(cfg.get("cooldown", 3))
        self.pending = None
        self.actions_since = self.cooldown   # 开场第一个动作就允许评价
        self.busy = False

    # ---------- 1. 记录 ----------
    def record(self, state, view, agent_name, character, cmd, info):
        self.actions_since += 1
        if cmd.get("action") != "use_spell":
            self.pending = None
            return
        target = cmd.get("target")
        hp_before = None
        if _key(target) == _key(view["actor"].get("guid")):
            hp_before = view["actor"].get("hp")
        else:
            for u in view["units"]:
                if _key(u["guid"]) == _key(target):
                    hp_before = u.get("hp")
        self.pending = {
            "session": state.get("session"), "agent": agent_name, "character": character,
            "spell": info.get("spell"), "target": target, "target_name": info.get("target_name"),
            "hp_before": hp_before, "line": info.get("say_en", ""),
            "log_t": self.d.combat_log_now(),
        }

    # ---------- 2. 结算效果 ----------
    @staticmethod
    def _outcome(p, state):
        t = None
        if _key(p["target"]) == _key(state["actor"].get("guid")):
            t = state["actor"]
        else:
            t = next((u for u in state.get("units", []) if _key(u["guid"]) == _key(p["target"])), None)
        if t is None or p["hp_before"] is None or t.get("hp") is None:
            return "result unclear"
        diff = p["hp_before"] - t["hp"]
        if (t.get("dead") or t["hp"] <= 0) and p["hp_before"] > 0:
            return f"it took {p['target_name']} down"
        if diff > 0:
            return f"it dealt {diff} damage ({p['target_name']} now {t['hp']}/{t.get('max_hp')})"
        if diff < 0:
            return f"it healed {p['target_name']} for {-diff}"
        return "no visible effect — probably a miss"

    def observe(self, state):
        p, self.pending = self.pending, None
        if not (p and self.enabled) or p["session"] != state.get("session"):
            return
        if self.busy or self.actions_since < self.cooldown or random.random() > self.chance:
            return
        reactor = self._pick_reactor(state, p["agent"])
        if reactor is None:
            return
        self.actions_since = 0
        self.busy = True
        outcome = self._outcome(p, state)
        log = self.d.combat_log_since(p.get("log_t"))
        if log:
            outcome += ". Game combat log: " + " | ".join(log[-4:])
        spoken_before = self.d.narrator.counter
        threading.Thread(target=self._react, args=(reactor, p, outcome, spoken_before), daemon=True).start()

    def _pick_reactor(self, state, actor_agent):
        """从在场的队友里挑一个由其他 AI 控制的角色。"""
        party = [state["actor"]] + [u for u in state.get("units", []) if u.get("ally") and not u.get("dead")]
        options = [m for m in self.d.ai_members([u.get("name") for u in party]) if m[0] != actor_agent]
        return random.choice(options) if options else None

    # ---------- 3. 生成并播出 ----------
    def _react(self, reactor, p, outcome, spoken_before):
        try:
            agent_name, character, onstage = reactor
            agent = self.d.get_agent(agent_name)
            acfg = self.d.cfg["agents"][agent_name]
            actor_cfg = self.d.cfg["agents"].get(p["agent"], {})
            actor_display = actor_cfg.get("display_name", p["agent"])
            system = build_reaction_system(acfg, agent_name, character, onstage)
            user = build_reaction_user(actor_display, p, outcome, list(self.d.recent))
            text = agent.complete(system, user, {"reaction": True})
            data = extract_json(text)
            say_en = str(data.get("say_en") or "").strip()
            say_zh = str(data.get("say_zh") or "").strip()
            if not (say_en and say_zh):
                return
            # 生成太慢、中间已经又播了不止一句：这句评价过时了，丢掉
            if self.d.narrator.counter > spoken_before + 1:
                print(f"  （{acfg.get('display_name', agent_name)} 的评价来晚了，跳过）")
                return
            display = acfg.get("display_name", agent_name)
            print(f"  💬 {display}（{character}）评价：{say_en} / {say_zh}")
            self.d.narrator.say(
                agent=display, character=character if onstage else f"{character}（场外）", color=acfg.get("color", "#ffffff"),
                say_en=say_en, say_zh=say_zh, voice_lang=acfg.get("voice_lang", "en"),
                voice_cfg=acfg, action="comment", tone=str(data.get("tone") or "").lower())
            self.d.recent.append(f"[{display} as {character}] commented on {actor_display}: \"{say_en}\"")
            self.d.fight_log.append(f"[{display}] 评价 {actor_display}：{say_zh}（{outcome}）")
        except Exception as e:
            print(f"  ! 队友评价失败（不影响游戏）：{e}")
        finally:
            self.busy = False
