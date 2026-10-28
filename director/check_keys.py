"""测试每个 AI 和语音能不能连上（只发一句很短的话，几乎不花钱）。

    python check_keys.py
"""

import os
import sys
import tempfile
import time

from providers import make_agent
from setup_env import HUMAN, TTS_KEYS, agent_has_key, load_config, load_keys

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    load_keys()
    cfg = load_config(os.path.join(HERE, "config.yaml"))
    agents = {k: v for k, v in (cfg.get("agents") or {}).items() if v.get("provider") != "mock"}
    used = {str(w) for w in (cfg.get("characters") or {}).values()} - {HUMAN}
    ok_all = True
    print("测试 AI 连接：\n")
    for name, acfg in agents.items():
        label = f"{acfg.get('display_name', name):9s} {acfg.get('model', ''):28s}"
        tag = "" if name in used else "（没分配角色）"
        if not agent_has_key(acfg):
            print(f"  -  {label} 没填 key（{acfg.get('api_key_env')}）{tag}")
            continue
        t0 = time.time()
        try:
            text = make_agent(name, acfg).complete("Reply with exactly: OK", "Say OK.")
            print(f"  ✓  {label} {time.time() - t0:4.1f}s  回复：{(text or '').strip()[:20]} {tag}")
        except Exception as e:
            ok_all = False
            print(f"  ✗  {label} 失败：{str(e)[:160]} {tag}")

    nar = cfg.get("narration") or {}
    engine = str(nar.get("tts_engine", "edge")).lower()
    print(f"\n测试语音（{engine}）：")
    need = TTS_KEYS.get(engine)
    if need and not os.environ.get(need):
        print(f"  -  没有 {need}，会自动改用免费的 edge-tts")
        engine = "edge"
    try:
        from narrator import Narrator
        tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(tmp, "overlay"))
        n = Narrator(dict(nar, overlay_port=0, tts=True, tts_engine=engine), tmp)
        first = next(iter(agents.values()), {})
        path = n._tts("语音测试，一二三。", first, "calm", os.path.join(tmp, "test"))
        print(f"  ✓  语音生成成功：{path}" if path else "  ✗  语音生成失败（只会有字幕）")
    except Exception as e:
        ok_all = False
        print(f"  ✗  语音失败：{str(e)[:160]}")
    print("\n全部正常！" if ok_all else "\n有失败的项目：检查 key 是否填对、账户里有没有余额、网络能不能访问对应网站。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
