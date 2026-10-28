"""MiniMax 试听（需要环境变量 MINIMAX_API_KEY）。

    python minimax_audition.py                 # 每个 AI 试听 5 个系统音色
    python minimax_audition.py --only claude   # 只试听 Claude 的
    python minimax_audition.py --tweak         # 微调变体：Claude 更凶、DeepSeek 更傻、Gemini 元气女孩
    python minimax_audition.py --design claude "成熟自信的御姐，声音低沉有磁性，说话干脆带点命令口吻"
                                               # 按文字描述设计一个专属声音（约 3 美元/个）

文件存到 auditions 文件夹：mm_<AI>_<音色>.mp3；挑中后把 voice_id 填进 config.yaml 的 minimax_voice。
"""

import argparse
import json
import os
import shutil
import tempfile
import urllib.request

import yaml

from narrator import Narrator

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "auditions")

# 每个 AI：试听台词、语气、候选系统音色
ROLES = {
    "claude": ("影心，给莱埃泽尔加血！(sighs) 这次给我按计划来，听见没有？", "frustrated", [
        "Chinese (Mandarin)_Mature_Woman", "Chinese (Mandarin)_Wise_Women", "Arrogant_Miss",
        "Chinese (Mandarin)_Reliable_Executive", "Chinese (Mandarin)_Sweet_Lady"]),
    "gpt": ("随便。(snorts) 我按我自己的来。", "sarcastic", [
        "Arrogant_Miss", "Chinese (Mandarin)_IntellectualGirl", "Chinese (Mandarin)_Laid_BackGirl",
        "Chinese (Mandarin)_Stubborn_Friend", "Chinese (Mandarin)_Crisp_Girl"]),
    "gemini": ("看到没看到没？！(laughs) 今晚最帅的一击，非我莫属！", "triumphant", [
        "Chinese (Mandarin)_Straightforward_Boy", "Chinese (Mandarin)_Unrestrained_Young_Man",
        "Chinese (Mandarin)_Pure-hearted_Boy", "Chinese (Mandarin)_Southern_Young_Man",
        "Chinese (Mandarin)_Gentle_Youth"]),
    "grok": ("(chuckle) 杀戮冲动又在我脑子里开会了。说真的，我有点想听听它的提案。", "sarcastic", [
        "Chinese (Mandarin)_Mature_Woman", "Chinese (Mandarin)_IntellectualGirl",
        "Chinese (Mandarin)_Stubborn_Friend", "Chinese (Mandarin)_ExplorativeGirl",
        "Chinese (Mandarin)_Reliable_Executive", "Chinese (Mandarin)_Warm_HeartedGirl"]),
    "qwen": ("正义的伙伴在此！看我这招——千问·破晓斩！", "excited", [
        "Chinese (Mandarin)_Crisp_Girl", "Chinese (Mandarin)_Warm_Girl", "Chinese (Mandarin)_ExplorativeGirl"]),
    "deepseek": ("嘿嘿，又打空了吧？(laughs) 略略略～还得看我的！", "playful", [
        "Chinese (Mandarin)_Cute_Spirit", "Chinese (Mandarin)_ExplorativeGirl", "Chinese (Mandarin)_Crisp_Girl",
        "Chinese (Mandarin)_Warm_Girl", "Chinese (Mandarin)_BashfulGirl"]),
}


CM = "Chinese (Mandarin)_"
# 微调变体：AI -> (台词, 语气, [(编号, 声音, 语速, 音高半音, voice_modify), ...])
TWEAKS = {
    # Grok（邪念）：邪魅、低沉、带笑意的反派女声
    "grok": ("呵呵……那个声音又在叫我了。它说，今晚该见点血。你们说，我是听，还是不听呢？", "sarcastic", [
        ("A", CM + "Mature_Woman", 0.92, -2, {"pitch": -40, "intensity": 30}),
        ("B", CM + "Mature_Woman", 0.88, -3, {"pitch": -60, "timbre": 40}),
        ("C", "Arrogant_Miss", 0.92, -2, {"pitch": -40}),
        ("D", CM + "Reliable_Executive", 0.9, -3, {"pitch": -50, "intensity": 40}),
        ("E", CM + "IntellectualGirl", 0.9, -4, {"pitch": -60, "timbre": 60}),
        ("F", CM + "Sweet_Lady", 0.88, -3, {"pitch": -50, "intensity": 50}),
    ]),
    "claude": ("影心，给莱埃泽尔加血！还愣着干什么？给我按计划来！", "frustrated", [
        ("A", CM + "Wise_Women", 1.0, 0, None),
        ("B", CM + "Wise_Women", 1.0, 0, {"pitch": -40}),
        ("C", CM + "Wise_Women", 1.0, 0, {"intensity": -60}),
        ("D", CM + "Wise_Women", 1.0, 0, {"intensity": 60}),
        ("E", CM + "Wise_Women", 1.08, -2, {"pitch": -30, "intensity": -40}),
        ("F", CM + "Wise_Women", 1.08, -2, {"pitch": -30, "intensity": 40}),
        ("G", "Arrogant_Miss", 1.05, 0, None),
        ("H", CM + "Mature_Woman", 1.05, -1, None),
    ]),
    "deepseek": ("诶嘿嘿？我、我刚才是打中了吧？略略略～", "playful", [
        ("A", CM + "Cute_Spirit", 1.0, 0, None),
        ("B", CM + "Cute_Spirit", 0.92, 2, None),
        ("C", CM + "Cute_Spirit", 0.9, 3, {"timbre": 60}),
        ("D", CM + "Cute_Spirit", 0.9, 3, {"timbre": -60}),
        ("E", CM + "Cute_Spirit", 0.88, 2, {"intensity": 60}),
        ("F", CM + "Cute_Spirit", 0.88, 2, {"intensity": -60}),
        ("G", CM + "BashfulGirl", 0.9, 3, None),
    ]),
    "gemini": ("冲冲冲！看我一口气冲到最前面！这一击绝对帅爆！", "excited", [
        ("A", CM + "Crisp_Girl", 1.15, 0, None),
        ("B", CM + "ExplorativeGirl", 1.15, 0, None),
        ("C", CM + "Warm_Girl", 1.15, 0, None),
        ("D", CM + "Warm_HeartedGirl", 1.15, 0, None),
        ("E", CM + "Crisp_Girl", 1.22, 2, {"intensity": -40}),
        ("F", CM + "ExplorativeGirl", 1.22, 2, {"intensity": -40}),
        ("G", CM + "Soft_Girl", 1.15, 1, None),
    ]),
}


def tweak_label(code, vid, speed, pitch, modify):
    m = ",".join(f"{k}{v:+d}" for k, v in (modify or {}).items())
    return f"{code}_{short(vid)}_speed{speed}_pitch{pitch:+d}" + (f"_{m}" if m else "")


def short(voice_id):
    return voice_id.replace("Chinese (Mandarin)_", "").replace(" ", "_")


def open_folder():
    try:
        os.startfile(OUT)
    except Exception:
        pass


def design(agent, prompt, cfg):
    key = os.environ.get("MINIMAX_API_KEY")
    if not key:
        print("没有设置环境变量 MINIMAX_API_KEY")
        return
    import re
    text = re.sub(r"\([a-z ]+\)\s*", "", (TWEAKS.get(agent) or ROLES[agent])[0])
    url = cfg["narration"].get("minimax_url", "https://api.minimax.io/v1/t2a_v2").replace("/t2a_v2", "/voice_design")
    req = urllib.request.Request(url, data=json.dumps({"prompt": prompt, "preview_text": text}).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    base = data.get("base_resp") or {}
    if base.get("status_code", 0) != 0:
        print(f"失败：{base.get('status_code')} {base.get('status_msg')}")
        return
    vid = data.get("voice_id")
    path = os.path.join(OUT, f"mm_design_{agent}_{vid}.mp3")
    with open(path, "wb") as f:
        f.write(bytes.fromhex(data.get("trial_audio") or ""))
    print(f"设计好了：voice_id = {vid}\n试听：{path}")
    print(f'满意的话，把 config.yaml 里 {agent} 的 minimax_voice 改成 "{vid}"，然后尽快用它说话（新声音长期不用可能会被清理）。')
    open_folder()


def main():
    from setup_env import load_keys
    load_keys()
    p = argparse.ArgumentParser()
    p.add_argument("--only", choices=sorted(ROLES))
    p.add_argument("--design", nargs=2, metavar=("AI", "描述"))
    p.add_argument("--tweak", action="store_true", help="生成微调变体")
    args = p.parse_args()
    cfg = yaml.safe_load(open(os.path.join(HERE, "config.yaml"), encoding="utf-8"))
    os.makedirs(OUT, exist_ok=True)

    if args.design:
        agent, prompt = args.design
        if agent not in ROLES:
            print(f"AI 名字只能是：{', '.join(ROLES)}")
            return
        design(agent, prompt, cfg)
        return

    tmp = tempfile.mkdtemp()
    os.makedirs(os.path.join(tmp, "overlay"))
    n = Narrator(dict(cfg.get("narration") or {}, overlay_port=0, tts=True), tmp)

    if args.tweak:
        for agent, (text, tone, variants) in TWEAKS.items():
            if args.only and args.only != agent:
                continue
            print(f"== {agent}（{tone}）：{text}")
            for code, vid, speed, pitch, modify in variants:
                label = tweak_label(code, vid, speed, pitch, modify)
                path = os.path.join(OUT, f"tw_{agent}_{label}.mp3")
                vcfg = {"minimax_voice": vid, "minimax_speed": speed, "minimax_pitch": pitch,
                        "minimax_modify": modify, "display_name": agent}
                try:
                    n._minimax(text, vcfg, tone, path)
                    print(f"  {label}")
                except Exception as e:
                    print(f"  {label}  失败：{e}")
            print()
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"已保存到 {OUT}，文件名 tw_<AI>_<编号>_...mp3，挑好告诉我编号")
        open_folder()
        return

    for agent, (text, tone, voices) in ROLES.items():
        if args.only and args.only != agent:
            continue
        print(f"== {agent}（{tone}）：{text}")
        for vid in voices:
            path = os.path.join(OUT, f"mm_{agent}_{short(vid)}.mp3")
            try:
                n._minimax(text, {"minimax_voice": vid, "display_name": agent}, tone, path)
                print(f"  {short(vid):24s} 情绪={n.last_style}")
            except Exception as e:
                print(f"  {short(vid):24s} 失败：{e}")
                if "MINIMAX_API_KEY" in str(e) or "1004" in str(e):
                    print("\nAPI key 没设置或不对：检查 MINIMAX_API_KEY，设置后要重新打开窗口。")
                    return
        print()
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"已保存到 {OUT}，文件名 mm_<AI>_<音色>.mp3")
    open_folder()


if __name__ == "__main__":
    main()
