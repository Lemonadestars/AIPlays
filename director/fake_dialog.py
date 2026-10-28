"""假对话：模拟游戏客户端写 AIPlays_dialog.json，测试调度器的剧情 / 议会流程。

    python fake_dialog.py --se-dir test_se
    （另一个窗口：python director.py --se-dir test_se）
"""

import argparse
import json
import os
import time

SCRIPT = [
    ("莱埃泽尔", "<i>Tsk'va（吉斯语：妈的！）</i>你不是奴隶 —— 真是维拉基斯保佑！我们一起也许还有活下去的机会。",
     ["你为什么会觉得我是一个奴隶？", "你是谁？", "我们必须搞清楚这是什么地方。", "你有什么建议？"]),
    ("莱埃泽尔", "我是莱埃泽尔，吉斯洋基战士。这艘船是夺心魔的。我们得找到控制台，否则就会一起坠毁。",
     ["好，一起去找控制台。", "[说服] 你先走，我殿后。", "我凭什么相信你？"]),
    ("莱埃泽尔", "别磨蹭了。", ["继续"]),
]


def write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--se-dir", default="test_se")
    args = p.parse_args()
    os.makedirs(args.se_dir, exist_ok=True)
    state_path = os.path.join(args.se_dir, "AIPlays_dialog.json")
    cmd_path = os.path.join(args.se_dir, "AIPlays_dialog_cmd.json")
    session, seq, last_cmd = str(int(time.time())), 0, -1

    def push(**st):
        nonlocal seq
        seq += 1
        write(state_path, dict(type="dialog", session=session, seq=seq, **st))

    for npc, line, options in SCRIPT:
        push(active=True, instance=6, npc=npc, line=line, show_answers=False, answers=[], speaker="邪念")
        time.sleep(2)
        answers = [{"idx": i, "slot": i + 1, "text": t, "enabled": True,
                    "tags": ["Skill:说服"] if t.startswith("[") else []} for i, t in enumerate(options)]
        push(active=True, instance=6, npc=npc, line=line, show_answers=True, answers=answers, speaker="邪念")
        print(f"{npc}：{line}\n  等待选择……")
        while True:
            try:
                cmd = json.load(open(cmd_path, encoding="utf-8"))
            except (FileNotFoundError, json.JSONDecodeError):
                cmd = None
            if cmd and cmd.get("session") == session and cmd.get("seq") != last_cmd:
                last_cmd = cmd["seq"]
                print(f"  选了：{options[cmd['answer_idx']]}")
                break
            time.sleep(0.3)
    push(active=False)
    print("对话结束")
    time.sleep(1)


if __name__ == "__main__":
    main()
