"""把解说时间轴转成双语 SRT 字幕，方便后期剪辑。

用法：
    python make_srt.py logs/timeline_20261023_201503.jsonl
    python make_srt.py logs/timeline_xxx.jsonl --offset 4.5   # 录制比调度器晚开 4.5 秒就填 4.5
"""

import argparse
import json
import os


def fmt(sec):
    sec = max(0.0, sec)
    h, rem = divmod(int(sec * 1000), 3600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("timeline")
    p.add_argument("--offset", type=float, default=0.0,
                   help="录制开始时间减去调度器启动时间（秒）；录制先开则填负数")
    p.add_argument("--min-duration", type=float, default=3.0)
    args = p.parse_args()

    with open(args.timeline, "r", encoding="utf-8") as f:
        entries = [json.loads(line) for line in f if line.strip()]

    out = []
    for i, e in enumerate(entries):
        start = e["t"] - args.offset
        end = start + max(e.get("duration", 0) + 1.0, args.min_duration)
        if i + 1 < len(entries):
            end = min(end, entries[i + 1]["t"] - args.offset - 0.05)
        speaker = f'[{e["agent"]} · {e["character"]}]'
        out.append(f"{i + 1}\n{fmt(start)} --> {fmt(end)}\n{speaker} {e['en']}\n{e['zh']}\n")

    srt_path = os.path.splitext(args.timeline)[0] + ".srt"
    with open(srt_path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(out))
    print(f"已生成 {srt_path}（{len(out)} 条）")


if __name__ == "__main__":
    main()
