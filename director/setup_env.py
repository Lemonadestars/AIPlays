"""启动前的准备工作（给新手的保险）：

1. 读 keys.env（API key 存在这里，不用折腾系统环境变量；这个文件不会上传到 GitHub）
2. 读 party.yaml（setup_party.py 生成的角色分配，覆盖 config.yaml 里的 characters）
3. 检查每个要用的 AI 有没有 key：没有的话，这个 AI 的角色这次改成由你操作，游戏不会卡住
4. 语音引擎缺 key 时自动退回免费的 edge-tts
"""

import os

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
KEYS_FILE = os.path.join(HERE, "keys.env")
PARTY_FILE = os.path.join(HERE, "party.yaml")
HUMAN = "human"

# 每个服务需要的环境变量（setup.ps1 和 check_keys.py 也用这份表）
TTS_KEYS = {"minimax": "MINIMAX_API_KEY", "azure": "AZURE_SPEECH_KEY", "openai": "OPENAI_API_KEY"}


def load_keys(path=KEYS_FILE):
    """KEY=VALUE 一行一个；# 开头是注释。已经在系统环境变量里的不覆盖。返回读到的 key 名。"""
    found = []
    if not os.path.exists(path):
        return found
    with open(path, encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v:
                found.append(k)
                os.environ.setdefault(k, v)
    return found


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if os.path.exists(PARTY_FILE):
        with open(PARTY_FILE, encoding="utf-8") as f:
            party = yaml.safe_load(f) or {}
        for k in ("characters", "default_agent", "human_name", "human_role"):
            if k in party:
                cfg[k] = party[k]
        print(f"已读取角色分配：{os.path.basename(PARTY_FILE)}")
    return cfg


def agent_has_key(acfg):
    if (acfg or {}).get("provider") == "mock":
        return True
    env = (acfg or {}).get("api_key_env")
    return bool(env and os.environ.get(env))


def apply_key_checks(cfg):
    """没 key 的 AI：它的角色改成 human（这次运行有效），并打印清楚的提示。"""
    agents = cfg.get("agents") or {}
    chars = cfg.get("characters") or {}
    missing = {}
    for char, who in list(chars.items()):
        who = str(who)
        if who == HUMAN:
            continue
        if who not in agents:
            print(f"  ! characters 里的 {char}: {who} —— config.yaml 的 agents 里没有 {who}，这个角色改由你操作")
            chars[char] = HUMAN
            continue
        if not agent_has_key(agents[who]):
            missing.setdefault(who, []).append(char)
            chars[char] = HUMAN
    default = str(cfg.get("default_agent") or HUMAN)
    if default != HUMAN and (default not in agents or not agent_has_key(agents[default])):
        missing.setdefault(default, []).append("（其他角色）")
        cfg["default_agent"] = HUMAN
    for who, cs in missing.items():
        env = (agents.get(who) or {}).get("api_key_env", "?")
        print(f"  ! 没找到 {who} 的 API key（{env}）：{'、'.join(map(str, cs))} 这次由你操作。"
              f"运行 setup.ps1 →“填写 API key”可以补上。")

    # 记忆总结用的 AI 没 key：换一个有 key 的，都没有就关掉记忆
    mem = cfg.setdefault("memory", {})
    if mem.get("enabled", True):
        cur = str(mem.get("agent", "deepseek"))
        if cur not in agents or not agent_has_key(agents[cur]):
            usable = [n for n, a in agents.items() if a.get("provider") != "mock" and agent_has_key(a)]
            if usable:
                mem["agent"] = usable[0]
                print(f"  （长期记忆改用 {usable[0]} 来总结，因为 {cur} 没有 key）")
            else:
                mem["enabled"] = False

    # 语音：缺 key 就退回免费的 edge-tts
    nar = cfg.setdefault("narration", {})
    engine = str(nar.get("tts_engine", "edge")).lower()
    need = TTS_KEYS.get(engine)
    if need and not os.environ.get(need):
        nar["tts_engine"] = "edge"
        print(f"  （没有 {need}，语音改用免费的微软 edge-tts。想要更有感情的 MiniMax 语音，在 setup.ps1 里填上 key）")

    ai_count = sum(1 for w in chars.values() if str(w) != HUMAN)
    if ai_count == 0:
        print("\n  ! 现在没有任何角色归 AI 控制（可能是一个 key 都没填）。先运行 setup.ps1 填 API key。\n")
    return cfg
