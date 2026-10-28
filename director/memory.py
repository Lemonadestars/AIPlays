"""长期记忆：每段对话 / 每场战斗结束后，让一个 AI 把值得记住的事总结成几条“关键记忆”，
存进 memories.json（重启、读档都还在），之后的剧情讨论和战斗解说都会带上相关的记忆。

只记游戏里真正发生过的事：AI 们是跟着剧情一点点知道的（比如看到影心后期剧情才知道她的身世）。
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from providers import extract_json

MEMORY_TEMPLATE = """You keep the long-term memory of an adventuring party in Baldur's Gate 3, played by four AIs
(Claude, GPT, Gemini, DeepSeek, Grok, Qwen, Mistral) and a human player ({human}) LIVE on a stream — an audience is watching right now.

You get what just happened ({kind}) and the memories the party already has. Write down 0-{max_new} NEW key memories
worth remembering for the rest of the adventure:
- story facts and revelations (who someone really is, secrets, backstories, quests, promises, betrayals),
- important choices the party made and why,
- memorable or funny moments between the AIs / {human} (a running joke, an argument, an epic crit, an embarrassing miss).
Only things that actually happened in what you were given — no guessing, no outside knowledge of the game's plot.
Skip trivia and anything already in the existing memories. Each memory: one short sentence in Simplified Chinese.
Call the AIs by their AI names (Claude / GPT / Gemini / DeepSeek / Grok / Qwen / Mistral) and the human player {human}; NPCs by name.

Reply with ONLY a JSON object:
{{"memories": [{{"text": "<one sentence>", "about": ["<names this memory is about>"]}}]}}"""

COMPACT_TEMPLATE = """You keep the long-term memory of an adventuring party in Baldur's Gate 3. The memory list is too long.
Merge the memories below into at most {target} memories: keep every important story fact, revelation, decision and the
best running jokes; merge duplicates; drop trivia. One short sentence each, Simplified Chinese, same naming style.

Reply with ONLY a JSON object:
{{"memories": [{{"text": "<one sentence>", "about": ["<names>"]}}]}}"""


class Memory:
    def __init__(self, director):
        self.d = director
        self.cfg = director.cfg.get("memory") or {}
        self.enabled = self.cfg.get("enabled", True)
        self.path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memories.json")
        self.lock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=1)   # 一条一条总结，不抢
        self.entries = []
        try:
            with open(self.path, encoding="utf-8") as f:
                self.entries = json.load(f).get("memories") or []
        except (FileNotFoundError, json.JSONDecodeError):
            pass

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"memories": self.entries}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def _agent(self):
        return self.d.get_agent(str(self.cfg.get("agent", "deepseek")))

    # ---------- 写 ----------
    def summarize_async(self, kind, lines):
        """kind: 'a conversation' / 'a fight'。lines: 这段经历的文字记录。"""
        if not self.enabled or len(lines) < int(self.cfg.get("min_lines", 3)):
            return
        self.pool.submit(self._summarize, kind, list(lines))

    def _summarize(self, kind, lines):
        try:
            with self.lock:
                existing = [e["text"] for e in self.entries[-60:]]
            system = MEMORY_TEMPLATE.format(human=self.d.human_name(), kind=kind,
                                            max_new=int(self.cfg.get("max_new", 3)))
            user = "EXISTING MEMORIES:\n" + ("\n".join(f"- {t}" for t in existing) or "(none yet)")
            user += f"\n\nWHAT JUST HAPPENED ({kind}):\n" + "\n".join(lines[-80:]) + "\n\nNew memories. JSON only."
            data = extract_json(self._agent().complete(system, user, {"memory": True}))
            new = []
            for m in data.get("memories") or []:
                text = str(m.get("text") or "").strip()
                if text and all(text != e["text"] for e in self.entries):
                    new.append({"t": time.strftime("%Y-%m-%d %H:%M"), "text": text,
                                "about": [str(a) for a in (m.get("about") or [])][:6]})
            if not new:
                return
            with self.lock:
                self.entries.extend(new)
                self._save()
            for m in new:
                print(f"  📌 记住了：{m['text']}")
            if len(self.entries) > int(self.cfg.get("max_entries", 150)):
                self._compact()
        except Exception as e:
            print(f"  ! 记忆总结失败（不影响游戏）：{e}")

    def _compact(self):
        """太多了：把最旧的一半合并成少量几条。"""
        with self.lock:
            half = len(self.entries) // 2
            old = self.entries[:half]
        target = max(10, half // 4)
        user = "\n".join(f"- {e['text']}" for e in old) + "\n\nMerged memories. JSON only."
        data = extract_json(self._agent().complete(COMPACT_TEMPLATE.format(target=target), user, {"memory": True}))
        merged = [{"t": old[-1]["t"], "text": str(m.get("text")).strip(),
                   "about": [str(a) for a in (m.get("about") or [])][:6]}
                  for m in data.get("memories") or [] if m.get("text")]
        if not merged:
            return
        with self.lock:
            self.entries = merged + self.entries[half:]
            self._save()
        print(f"  📌 记忆整理：{half} 条旧记忆合并成 {len(merged)} 条")

    # ---------- 读 ----------
    def relevant(self, names=(), n=20):
        """挑出和现在相关的记忆：先挑提到这些名字的，剩下的名额给最近的，按时间顺序返回。"""
        if not self.enabled:
            return []
        with self.lock:
            entries = list(enumerate(self.entries))
        names = [x for x in names if x]

        def hit(e):
            return any(nm in e["text"] or any(nm in a or a in nm for a in e.get("about") or [] if a)
                       for nm in names)
        picked = [i for i, e in entries if hit(e)][-n // 2:] if names else []
        for i, _ in reversed(entries):
            if len(picked) >= n:
                break
            if i not in picked:
                picked.append(i)
        return [self.entries[i]["text"] for i in sorted(picked)]
