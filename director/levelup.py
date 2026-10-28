"""升级：角色可以升级时，让控制它的 AI 自己决定怎么升（职业、子职业、专长 / 属性、法术），
用语音解释给观众听，并把方案打印出来、存进 levelup_plans.txt。

现在由你照着方案在升级界面里点（升级界面的自动操作以后再做）。
需要 mod 导出的角色卡（AIPlays_party.json 里的 members）。
"""

import os
import time
from concurrent.futures import ThreadPoolExecutor

from providers import extract_json

HUMAN = "human"

LEVELUP_TEMPLATE = """You are {agent_display}, an AI playing Baldur's Gate 3 (5e rules) LIVE on a stream — an audience is watching right now.
You control {character}, who can level up right now. YOU decide how.

Your persona: {persona}

Decide everything this level-up needs: class to put the level in (usually the current class; multiclass only with
a good reason), subclass if this level grants one, feat or +2/+1 ability improvement if this level grants one,
new spells / cantrips / maneuvers etc. if any. Use real BG3 names for everything. Think about the party
(who tanks, heals, does damage), your character's build so far, and your persona — and make it fun to watch.

Reply with ONLY a JSON object:
{{"plan_zh": "<the complete level-up choices as a short checklist in Simplified Chinese, using the game's Chinese names
where you know them, e.g. '职业：圣武士 3；子职业：…；法术：…'>",
  "plan_en": "<the same checklist in English>",
  "say_zh": "<one or two spoken sentences to viewers explaining the most interesting pick, in character, max 35 words>",
  "say_en": "<same in English>",
  "tone": "<calm | confident | excited | triumphant | playful | sarcastic>"}}
Rules: say_zh is read aloud; call teammates by AI names (Claude / GPT / Gemini / DeepSeek / Grok / Qwen / Mistral) and the human player {human}."""


def fmt_sheet(m):
    cls = ", ".join(f"{c.get('class') or '?'}{' (' + c['subclass'] + ')' if c.get('subclass') else ''} {c.get('level')}"
                    for c in m.get("classes") or []) or "?"
    ab = m.get("abilities") or {}
    ab_txt = " ".join(f"{k[:3].upper()} {v}" for k, v in ab.items() if v is not None)
    return f"{m.get('name')}: level {m.get('level')} — {cls}" + (f" — {ab_txt}" if ab_txt else "")


class LevelUp:
    def __init__(self, director):
        self.d = director
        self.cfg = director.cfg.get("levelup") or {}
        self.enabled = self.cfg.get("enabled", True)
        self.plans_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "levelup_plans.txt")
        self.done = set()          # (角色, 目标等级)
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.next_check = 0

    def poll(self):
        if not self.enabled or time.time() < self.next_check:
            return
        self.next_check = time.time() + 3
        party = self.d.read_json(os.path.join(self.d.se_dir, "AIPlays_party.json")) or {}
        members = party.get("members") or []
        for m in members:
            lvl, avail = m.get("level"), m.get("available_level")
            if not (isinstance(lvl, int) and isinstance(avail, int) and avail > lvl):
                continue
            key = (m.get("name"), lvl + 1)
            if key in self.done:
                continue
            self.done.add(key)
            agent_name = self.d.assignment(m.get("name"))
            if agent_name == HUMAN or agent_name not in (self.d.cfg.get("agents") or {}):
                continue
            self.pool.submit(self._decide, agent_name, m, members)

    def _decide(self, agent_name, m, members):
        try:
            acfg = self.d.cfg["agents"][agent_name]
            display = acfg.get("display_name", agent_name)
            character = m.get("name")
            system = LEVELUP_TEMPLATE.format(agent_display=display, character=character,
                                             persona=acfg.get("persona", ""), human=self.d.human_name())
            lines = [f"LEVEL UP: {character} goes from level {m.get('level')} to {m.get('level', 0) + 1}.",
                     "YOUR CHARACTER: " + fmt_sheet(m), "", "PARTY:"]
            for o in members:
                who = self.d.assignment(o.get("name"))
                by = self.d.human_name() if who == HUMAN else (self.d.cfg.get("agents") or {}).get(who, {}).get(
                    "display_name", who)
                lines.append(f"- {fmt_sheet(o)} (played by {by})")
            mem = self.d.memory.relevant([character], 10)
            if mem:
                lines += ["", "KEY MEMORIES:"] + [f"- {x}" for x in mem]
            lines += ["", "Your level-up. JSON only."]
            data = extract_json(self.d.get_agent(agent_name).complete(system, "\n".join(lines), {"levelup": True}))
            plan_zh = str(data.get("plan_zh") or "").strip()
            plan_en = str(data.get("plan_en") or "").strip()
            print(f"\n⬆  {display} 给 {character} 的升级方案（{m.get('level')} → {m.get('level', 0) + 1}）：\n   {plan_zh}\n   {plan_en}")
            with open(self.plans_path, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] {display} / {character} "
                        f"{m.get('level')}→{m.get('level', 0) + 1}\n  {plan_zh}\n  {plan_en}\n\n")
            self.d.story.note(f"[{display} 给 {character} 升级] {plan_zh}")
            if data.get("say_zh") and data.get("say_en"):
                self.d.speak(agent=display, character=character, color=acfg.get("color", "#ffffff"),
                             say_en=str(data["say_en"]), say_zh=str(data["say_zh"]),
                             voice_lang=acfg.get("voice_lang", "zh"), voice_cfg=acfg, action="levelup",
                             tone=str(data.get("tone") or "").lower())
        except Exception as e:
            print(f"  ! 升级决定失败（不影响游戏）：{e}")
