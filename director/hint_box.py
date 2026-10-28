"""导演提示小窗口：输入一句话发给 AI 们（写进 hint.txt，调度器下一步就会读到）。

双击 hint_box.bat 打开。Ctrl+Enter 发送，Esc 清空。
"""

import os
import tkinter as tk
from tkinter import font

HERE = os.path.dirname(os.path.abspath(__file__))
HINT = os.path.join(HERE, "hint.txt")
PAUSE = os.path.join(HERE, "paused.txt")
HEADER = ("# 导演提示：下面的内容会发给 AI 们（以 # 开头的行不会）。可以直接改这个文件，也可以用 hint_box 小窗口。\n")

BG, PANEL, INK, SOFT, GOLD = "#17120e", "#221b15", "#f1e8d4", "#a99c86", "#c9a45c"


def read_hint():
    try:
        with open(HINT, encoding="utf-8") as f:
            return " ".join(x.strip() for x in f.read().splitlines() if x.strip() and not x.strip().startswith("#"))
    except FileNotFoundError:
        return ""


def write_hint(text):
    tmp = HINT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(HEADER + (text.strip() + "\n" if text.strip() else ""))
    os.replace(tmp, HINT)


def main():
    root = tk.Tk()
    root.title("导演提示")
    root.configure(bg=BG)
    root.geometry("560x260")
    root.minsize(360, 200)
    root.attributes("-topmost", True)

    base = font.nametofont("TkDefaultFont").actual("family")
    f_body = (base, 12)
    f_small = (base, 10)

    tk.Label(root, text="给 AI 们的一句提示（它们会当成自己想到的）", bg=BG, fg=SOFT, font=f_small,
             anchor="w").pack(fill="x", padx=14, pady=(12, 4))

    box = tk.Text(root, height=4, wrap="word", bg=PANEL, fg=INK, insertbackground=GOLD, relief="flat",
                  font=f_body, padx=10, pady=8, highlightthickness=1, highlightbackground="#3a2f25",
                  highlightcolor=GOLD)
    box.pack(fill="both", expand=True, padx=14)
    box.focus_set()

    status = tk.Label(root, text="", bg=BG, fg=SOFT, font=f_small, anchor="w", justify="left", wraplength=420)
    status.pack(fill="x", padx=14, pady=(6, 0))

    def refresh():
        cur = read_hint()
        status.config(text=("当前提示：" + cur) if cur else "当前没有提示", fg=GOLD if cur else SOFT)

    def send(_=None):
        text = box.get("1.0", "end").strip()
        if not text:
            return "break"
        write_hint(text)
        box.delete("1.0", "end")
        refresh()
        return "break"

    def clear(_=None):
        write_hint("")
        refresh()
        return "break"

    row = tk.Frame(root, bg=BG)
    row.pack(fill="x", padx=14, pady=10)
    btn = dict(relief="flat", font=f_small, padx=14, pady=4, cursor="hand2", bd=0)
    tk.Button(row, text="发送（Ctrl+Enter）", command=send, bg=GOLD, fg=BG, activebackground="#e0bd73",
              **btn).pack(side="left")
    tk.Button(row, text="清除提示（Esc）", command=clear, bg=PANEL, fg=INK, activebackground="#3a2f25",
              activeforeground=INK, **btn).pack(side="left", padx=8)
    paused = tk.BooleanVar(value=os.path.exists(PAUSE) and open(PAUSE, encoding="utf-8").read().strip() == "1")

    def toggle_pause():
        with open(PAUSE, "w", encoding="utf-8") as f:
            f.write("1" if paused.get() else "0")
    tk.Checkbutton(row, text="暂停 AI（全部我来操作）", variable=paused, command=toggle_pause,
                   bg=BG, fg=SOFT, selectcolor=PANEL, activebackground=BG, activeforeground=INK,
                   font=f_small).pack(side="right", padx=(8, 0))
    top = tk.BooleanVar(value=True)
    tk.Checkbutton(row, text="置顶", variable=top, command=lambda: root.attributes("-topmost", top.get()),
                   bg=BG, fg=SOFT, selectcolor=PANEL, activebackground=BG, activeforeground=INK,
                   font=f_small).pack(side="right")

    box.bind("<Control-Return>", send)
    root.bind("<Escape>", clear)

    def tick():                     # 别处改了 hint.txt（比如直接编辑文件）也能看到
        refresh()
        root.after(2000, tick)
    tick()
    root.mainloop()


if __name__ == "__main__":
    main()
