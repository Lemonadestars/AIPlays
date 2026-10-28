"""不开游戏，模拟 mod 那一端，用来测试调度器、API key、字幕和语音。

用法（开两个终端）：
    python director.py --se-dir test_se
    python fake_game.py --se-dir test_se
"""

import argparse
import json
import os
import time

# 四个角色各对应一家 AI（见 config.yaml 的 characters），轮流行动
PARTY = [
    {"guid": "fake-tav", "name": "邪念", "hp": 14, "max_hp": 14, "pos": [-1, 0, 1]},
    {"guid": "fake-shadowheart", "name": "影心", "hp": 14, "max_hp": 14, "pos": [0, 0, 0]},
    {"guid": "fake-laezel", "name": "莱埃泽尔", "hp": 20, "max_hp": 20, "pos": [1, 0, 2]},
    {"guid": "fake-astarion", "name": "阿斯代伦", "hp": 12, "max_hp": 12, "pos": [-2, 0, -1]},
]
ENEMIES = [
    {"guid": "fake-goblin", "name": "地精", "hp": 14, "max_hp": 14, "pos": [6, 0, 3]},
    {"guid": "fake-archer", "name": "地精弓箭手", "hp": 12, "max_hp": 12, "pos": [9, 2, -2]},
]
SPELLS = ["Target_MainHandAttack", "Projectile_GuidingBolt", "Target_CureWounds", "Shout_Dash"]


def dist(a, b):
    return round(((a[0] - b[0]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5, 1)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--se-dir", default="test_se")
    p.add_argument("--turns", type=int, default=4)  # 默认 4 回合 = 四个角色各一回合
    args = p.parse_args()
    os.makedirs(args.se_dir, exist_ok=True)
    state_path = os.path.join(args.se_dir, "AIPlays_state.json")
    cmd_path = os.path.join(args.se_dir, "AIPlays_command.json")

    session, seq = f"fake-{int(time.time())}", 0
    for turn in range(args.turns):
        actor = PARTY[turn % len(PARTY)]
        actions, last = 0, None
        while True:
            seq += 1
            units = []
            for u in PARTY + ENEMIES:
                if u is actor:
                    continue
                units.append(dict(u, enemy=u in ENEMIES, ally=u in PARTY, dead=u["hp"] <= 0,
                                  dist=dist(actor["pos"], u["pos"])))
            state = {
                "session": session, "seq": seq, "type": "turn", "actions_taken": actions, "last_result": last,
                "actor": dict(actor, spells=SPELLS, resources={
                    "action": 1 if actions == 0 else 0, "bonus_action": 1, "movement": 9.0,
                    "spell_slots": [2, 0, 0]}),
                "units": units,
            }
            with open(state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
            print(f"[fake] #{seq} 等待 {actor['name']} 的指令…")

            while True:
                try:
                    with open(cmd_path, encoding="utf-8") as f:
                        cmd = json.load(f)
                    if cmd.get("session") == session and cmd.get("seq") == seq:
                        break
                except (FileNotFoundError, json.JSONDecodeError):
                    pass
                time.sleep(0.2)

            print(f"[fake] 收到：{cmd}")
            actions += 1
            if cmd["action"] == "use_spell":
                target = next((u for u in PARTY + ENEMIES if u["guid"] == cmd["target"]), actor)
                target["hp"] = max(0, target["hp"] - 4)
                last = f"used {cmd['spell']}"
            elif cmd["action"] == "move":
                actor["pos"] = cmd["position"]
                last = "moved"
            if cmd["action"] == "end_turn" or actions >= 4:
                break
    print("[fake] 模拟结束")


if __name__ == "__main__":
    main()
