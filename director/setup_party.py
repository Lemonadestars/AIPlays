"""角色分配向导：谁来玩哪个角色（某个 AI，或者你自己）。结果存进 party.yaml。

    python setup_party.py
"""

import os
import sys

import yaml

from setup_env import HUMAN, PARTY_FILE, agent_has_key, load_config, load_keys

HERE = os.path.dirname(os.path.abspath(__file__))

# (中文名, 英文名[, 其他写法…])  —— 游戏里显示的名字包含这些字就算匹配
COMPANIONS = [
    ("影心", "Shadowheart"),
    ("莱埃泽尔", "Lae'zel"),
    ("阿斯代伦", "Astarion"),
    ("盖尔", "Gale"),
    ("威尔", "Wyll"),
    ("卡菈克", "Karlach", "卡拉克"),
    ("贾希拉", "Jaheira"),
    ("明斯克", "Minsc"),
    ("哈尔辛", "Halsin"),
    ("明莎拉", "Minthara"),
]


def ask(prompt, default=""):
    try:
        v = input(prompt).strip()
    except EOFError:
        v = ""
    return v or default


def main():
    load_keys()
    cfg = load_config(os.path.join(HERE, "config.yaml"))
    agents = {k: v for k, v in (cfg.get("agents") or {}).items() if v.get("provider") != "mock"}
    names = list(agents)
    chars = {str(k): str(v) for k, v in (cfg.get("characters") or {}).items()}

    print("\n可选的玩家：")
    print(f"  0. 你自己（human）")
    for i, n in enumerate(names, 1):
        ok = "已填 key" if agent_has_key(agents[n]) else "还没填 key"
        print(f"  {i}. {agents[n].get('display_name', n)}  （{agents[n].get('model')}，{ok}）")
    print("每个角色输入编号，直接回车 = 保持现在的设置。\n")

    def pick(label, current):
        cur_txt = "你自己" if current == HUMAN else (agents.get(current, {}).get("display_name", current))
        while True:
            v = ask(f"  {label}（现在：{cur_txt}）> ")
            if not v:
                return current
            if v.isdigit() and 0 <= int(v) <= len(names):
                return HUMAN if int(v) == 0 else names[int(v) - 1]
            print("    请输入上面列出的编号")

    result = {}
    # 主角：名字是玩家自己起的
    hero_keys = [k for k, v in chars.items() if not any(k in c for c in COMPANIONS)]
    hero_default = hero_keys[0] if hero_keys else ""
    print("主角：你在创建角色时起的名字（邪念的默认名就是“邪念”）。")
    hero = ask(f"  主角在游戏里叫什么？（直接回车 = {hero_default or '跳过'}）> ", hero_default)
    if hero:
        who = pick(hero, chars.get(hero, HUMAN))
        result[hero] = who
        if hero == "邪念":
            result["Dark Urge"] = who
    print()
    for names_tuple in COMPANIONS:
        cur = next((chars[n] for n in names_tuple if n in chars), str(cfg.get("default_agent") or HUMAN))
        who = pick(f"{names_tuple[0]}（{names_tuple[1]}）", cur)
        for n in names_tuple:
            result[n] = who

    print()
    human_name = ask(f"AI 们怎么称呼你？（直接回车 = {cfg.get('human_name') or 'User'}）> ",
                     str(cfg.get("human_name") or "User"))
    default_agent = HUMAN   # 上面没列到的角色都归你

    with open(PARTY_FILE, "w", encoding="utf-8") as f:
        f.write("# 角色分配（setup_party.py 生成，会覆盖 config.yaml 里的 characters）。也可以直接改这个文件。\n")
        yaml.safe_dump({"human_name": human_name, "default_agent": default_agent, "characters": result},
                       f, allow_unicode=True, sort_keys=False)
    print(f"\n已保存到 {PARTY_FILE}。重启调度器后生效。")
    no_key = sorted({w for w in result.values() if w != HUMAN and not agent_has_key(agents.get(w))})
    if no_key:
        print("注意：这些 AI 还没填 key，没填之前它们的角色由你操作：" + "、".join(no_key))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
