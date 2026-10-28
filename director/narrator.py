"""解说层：双语字幕（OBS 浏览器源）+ TTS 语音 + 时间轴日志。

- 字幕页面：本地小服务器提供 overlay/overlay.html，OBS 里加一个浏览器源指向它
- 语音：edge-tts 生成 mp3，由字幕页面播放，所以 OBS 会把声音一起录进去
- 时间轴：logs/timeline_*.jsonl，录完可以用 make_srt.py 生成双语字幕文件
"""

import asyncio
import functools
import json
import os
import re
import threading
import time
import urllib.request
import wave
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class _QuietHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, *args):
        pass


# 语气 -> edge-tts 的语速 / 音高微调（让同一个声音有起伏）
TONES = {
    "calm": ("-4%", "-2Hz"),
    "confident": ("+0%", "+0Hz"),
    "excited": ("+12%", "+8Hz"),
    "triumphant": ("+8%", "+10Hz"),
    "tense": ("+10%", "+2Hz"),
    "worried": ("+4%", "+5Hz"),
    "frustrated": ("+6%", "-4Hz"),
    "playful": ("+6%", "+6Hz"),
    "sarcastic": ("-3%", "-3Hz"),
}
DEFAULT_TONE = "confident"

# 语气 -> ChatTTS 参数（必须是整数）：语速 0~10（5 正常）、口语化 0~9、笑声 0~9、停顿 0~9
CHATTTS_TONES = {
    "calm":       {"speed": 4, "oral": 1, "laugh": 0, "break": 3},
    "confident":  {"speed": 5, "oral": 2, "laugh": 0, "break": 2},
    "excited":    {"speed": 6, "oral": 4, "laugh": 1, "break": 1},
    "triumphant": {"speed": 6, "oral": 3, "laugh": 2, "break": 1},
    "tense":      {"speed": 6, "oral": 1, "laugh": 0, "break": 1},
    "worried":    {"speed": 5, "oral": 3, "laugh": 0, "break": 3},
    "frustrated": {"speed": 5, "oral": 4, "laugh": 0, "break": 2},
    "playful":    {"speed": 6, "oral": 6, "laugh": 2, "break": 2},
    "sarcastic":  {"speed": 4, "oral": 3, "laugh": 1, "break": 3},
}

_MP3_BITRATES = {
    1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],   # MPEG1 Layer III
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],       # MPEG2/2.5 Layer III
}


def mp3_duration(path):
    """按第一帧的码率估算 mp3 时长（edge-tts 和 OpenAI 输出的都是固定码率）。失败返回 None。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
        pos = 0
        if data[:3] == b"ID3":
            size = (data[6] << 21) | (data[7] << 14) | (data[8] << 7) | data[9]
            pos = 10 + size
        for i in range(pos, min(len(data) - 4, pos + 4096)):
            if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
                version = (data[i + 1] >> 3) & 0x3          # 3 = MPEG1, 2 = MPEG2, 0 = MPEG2.5
                layer = (data[i + 1] >> 1) & 0x3            # 1 = Layer III
                idx = (data[i + 2] >> 4) & 0xF
                if layer != 1 or idx in (0, 15) or version == 1:
                    continue
                kbps = _MP3_BITRATES[1 if version == 3 else 2][idx]
                return (len(data) - i) * 8 / (kbps * 1000)
    except OSError:
        pass
    return None


# 语气 -> Azure 情感风格（按优先顺序；会自动选这个声音支持的第一个）和强度（0.01~2）
AZURE_TONES = {
    "calm":       (["calm", "gentle", "friendly", "chat"], 1.0),
    "confident":  (["friendly", "chat", "serious", "assistant"], 1.0),
    "excited":    (["excited", "sports-commentary-excited", "cheerful"], 1.5),
    "triumphant": (["excited", "sports-commentary-excited", "cheerful", "shouting"], 1.7),
    "tense":      (["serious", "fearful", "terrified"], 0.9),
    "worried":    (["fearful", "sad", "terrified"], 1.2),
    "frustrated": (["disgruntled", "angry", "unfriendly"], 1.3),
    "playful":    (["cheerful", "excited", "affectionate", "friendly"], 1.5),
    "sarcastic":  (["unfriendly", "disgruntled", "serious"], 1.2),
}


# 语气 -> MiniMax 情绪参数 + 语速倍数
MINIMAX_TONES = {
    "calm":       ("calm", 0.95),
    "confident":  (None, 1.0),        # None = 让模型自己判断
    "excited":    ("happy", 1.1),
    "triumphant": ("happy", 1.08),
    "tense":      ("fearful", 1.1),
    "worried":    ("fearful", 1.05),
    "frustrated": ("angry", 1.05),
    "playful":    ("happy", 1.05),
    "sarcastic":  ("disgusted", 1.0),
}

# 台词里可以带的音效标签（MiniMax speech-2.8 会真的笑/叹气）；字幕里要去掉
SOUND_TAGS = ("laughs", "chuckle", "sighs", "gasps", "snorts", "groans", "humming", "breath",
              "emm", "coughs", "clear-throat", "pant", "inhale", "exhale", "sniffs", "lip-smacking")
_SOUND_TAG_RE = re.compile(r"\s*[(（](?:" + "|".join(re.escape(t) for t in SOUND_TAGS) + r")[)）]\s*", re.I)


_CJK_SPACE_RE = re.compile(r"(?<=[\u3000-\u9fff\uff00-\uffef])\s+|\s+(?=[\u3000-\u9fff\uff00-\uffef])")


def strip_sound_tags(text):
    out = _SOUND_TAG_RE.sub(" ", text or "")
    return _CJK_SPACE_RE.sub("", out).strip()


def _xml_escape(text):
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&apos;"))


def wav_duration(path):
    """读 WAV 头算时长（ChatTTS 输出的是 32 位浮点 WAV，标准库 wave 读不了，所以手动解析）。"""
    try:
        with open(path, "rb") as f:
            data = f.read()
        if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
            return None
        pos, byte_rate, data_size = 12, None, None
        while pos + 8 <= len(data):
            cid, size = data[pos:pos + 4], int.from_bytes(data[pos + 4:pos + 8], "little")
            if cid == b"fmt ":
                byte_rate = int.from_bytes(data[pos + 16:pos + 20], "little")
            elif cid == b"data":
                data_size = min(size, len(data) - pos - 8)
            pos += 8 + size + (size & 1)
        if byte_rate and data_size is not None:
            return data_size / byte_rate
    except OSError:
        pass
    return None


def estimate_speech_seconds(text, lang):
    if lang == "zh":
        return len(text) / 4.5 + 0.6          # 中文大约每秒 4~5 个字
    return len(text.split()) / 2.6 + 0.6      # 英文大约每秒 2.5~3 个词


class Narrator:
    def __init__(self, cfg, base_dir):
        self.cfg = cfg or {}
        self.port = int(self.cfg.get("overlay_port", 8765))
        self.tts_enabled = bool(self.cfg.get("tts", True))
        self.wait_for_speech = bool(self.cfg.get("wait_for_speech", True))
        self.max_wait = float(self.cfg.get("max_wait_sec", 12))
        self.hold = float(self.cfg.get("subtitle_hold_sec", 9))

        self.overlay_dir = os.path.join(base_dir, "overlay")
        self.audio_dir = os.path.join(self.overlay_dir, "audio")
        self.log_dir = os.path.join(base_dir, "logs")
        os.makedirs(self.audio_dir, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)

        self.start = time.time()
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.timeline_path = os.path.join(self.log_dir, f"timeline_{stamp}.jsonl")
        self.counter = 0
        self.busy_until = 0.0
        self.lock = threading.Lock()   # 队友评价在后台线程里说话，要和主线程排队
        self._edge_tts = None
        self._openai = None
        self._warned = set()
        self.engine = str(self.cfg.get("tts_engine", "edge")).lower()
        self.openai_model = self.cfg.get("openai_tts_model", "gpt-4o-mini-tts")
        self.chattts_url = self.cfg.get("chattts_url", "http://127.0.0.1:5000/generate_audio")
        self.chattts_refine = bool(self.cfg.get("chattts_refine", True))
        self.base_dir = base_dir
        self.azure_region = os.environ.get("AZURE_SPEECH_REGION") or self.cfg.get("azure_region", "eastus")
        self.azure_endpoint = self.cfg.get("azure_endpoint") or f"https://{self.azure_region}.tts.speech.microsoft.com"
        self._azure_styles = None   # 声音名 -> 支持的情感风格列表（第一次用时向 Azure 查询）
        self.last_style = None
        self.minimax_url = self.cfg.get("minimax_url", "https://api.minimax.io/v1/t2a_v2")
        self.minimax_model = self.cfg.get("minimax_model", "speech-2.8-turbo")

        self._write_current({"id": 0})
        self._start_server()

    # ---------- 本地服务器 ----------
    def _start_server(self):
        """字幕页面的本地服务器。端口被占用（比如旧的调度器窗口还开着）就每 3 秒重试，
        旧窗口一关就自动接上，不会出现“两个都没有”的情况。"""
        if not self.port:
            return
        handler = functools.partial(_QuietHandler, directory=self.overlay_dir)

        def run():
            warned = False
            while True:
                try:
                    server = ThreadingHTTPServer(("127.0.0.1", self.port), handler)
                except OSError as e:
                    if not warned:
                        print(f"  ! 字幕服务器端口 {self.port} 被占用（是不是还开着另一个调度器窗口？）"
                              f"——关掉那个窗口后会自动接上：{e}")
                        warned = True
                    time.sleep(3)
                    continue
                if warned:
                    print(f"  字幕服务器已接上：http://localhost:{self.port}/overlay.html")
                server.serve_forever()
        threading.Thread(target=run, daemon=True).start()

    def _write_current(self, data):
        path = os.path.join(self.overlay_dir, "current.json")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)

    # ---------- TTS ----------
    def _warn_once(self, key, msg):
        if key not in self._warned:
            self._warned.add(key)
            print(msg)

    @staticmethod
    def _combine(base, delta, unit):
        """把 AI 的基础值（如 "+12Hz"）和语气的调整（如 "+8Hz"）加起来。"""
        def num(v):
            try:
                return int(str(v or "0").replace(unit, "").strip() or 0)
            except ValueError:
                return 0
        total = num(base) + num(delta)
        return f"{total:+d}{unit}"

    def _edge(self, text, voice, tone, out_path, base_pitch=None, base_rate=None):
        if self._edge_tts is None:
            import edge_tts
            self._edge_tts = edge_tts
        rate, pitch = TONES.get(tone, TONES[DEFAULT_TONE])
        rate = self._combine(base_rate, rate, "%")
        pitch = self._combine(base_pitch, pitch, "Hz")

        async def run(v):
            await self._edge_tts.Communicate(text, v, rate=rate, pitch=pitch).save(out_path)

        try:
            asyncio.run(run(voice))
        except Exception as e:
            # 声音名写错或者这个声音不可用：退回一个肯定存在的声音
            fallback = "zh-CN-YunxiNeural" if voice.startswith("zh") else "en-US-GuyNeural"
            if voice == fallback:
                raise
            self._warn_once("voice:" + voice, f"  ! 声音 {voice} 不可用（{e}），改用 {fallback}")
            asyncio.run(run(fallback))
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0

    def _openai_tts(self, text, voice, tone, style, out_path):
        if self._openai is None:
            from openai import OpenAI
            self._openai = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        instructions = f"Tone: {tone}. {style}".strip()
        resp = self._openai.audio.speech.create(
            model=self.openai_model, voice=voice or "ash", input=text,
            instructions=instructions, response_format="mp3")
        with open(out_path, "wb") as f:
            f.write(resp.content)
        return os.path.getsize(out_path) > 0

    def _chattts(self, text, voice_cfg, tone, out_path):
        """调用 ChatTTS-Enhanced 的 api.py（POST /generate_audio，返回 24kHz wav）。"""
        p = CHATTTS_TONES.get(tone, CHATTTS_TONES[DEFAULT_TONE])
        body = {
            "text": text,
            "speed_slider": p["speed"],
            "temperature": float(voice_cfg.get("chattts_temperature", 0.3)),   # 低一点音色更稳
            "top_P": 0.7, "top_K": 20,
            "refine_text_flag": self.chattts_refine,   # 让模型自己加笑声、停顿、语气词
            "refine_oral": p["oral"], "refine_laugh": p["laugh"], "refine_break": p["break"],
        }
        speaker = voice_cfg.get("chattts_speaker")
        if speaker:
            # 用导出的音色文件（.pt）
            path = speaker if os.path.isabs(speaker) else os.path.join(self.base_dir, speaker)
            if not os.path.exists(path):
                raise FileNotFoundError(f"音色文件不存在：{path}")
            body.update(emb_upload=True, emb_upload_path=path)
        else:
            # 用种子：同一个种子永远是同一个声音（在 ChatTTS 网页界面里试听挑选）
            body["audio_profile_path"] = int(voice_cfg.get("chattts_seed", 2222))
        req = urllib.request.Request(self.chattts_url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        if not data.startswith(b"RIFF"):
            raise RuntimeError(f"ChatTTS 返回的不是音频：{data[:200]!r}")
        with open(out_path, "wb") as f:
            f.write(data)
        return True

    def _azure_request(self, path, data=None, headers=None):
        key = os.environ.get("AZURE_SPEECH_KEY")
        if not key:
            raise RuntimeError("没有设置环境变量 AZURE_SPEECH_KEY")
        h = {"Ocp-Apim-Subscription-Key": key, "User-Agent": "AIPlays"}
        h.update(headers or {})
        req = urllib.request.Request(self.azure_endpoint + path, data=data, headers=h)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()

    def _styles_for(self, voice):
        if self._azure_styles is None:
            self._azure_styles = {}
            try:
                for v in json.loads(self._azure_request("/cognitiveservices/voices/list")):
                    self._azure_styles[v.get("ShortName")] = [x.lower() for x in (v.get("StyleList") or [])]
            except Exception as e:
                self._warn_once("azure-list", f"  ! 查询 Azure 声音风格失败（{e}），只用语速音高")
        return self._azure_styles.get(voice, [])

    def _azure(self, text, voice_cfg, tone, out_path):
        """Azure 语音：同样的微软声音，但能用真正的情感风格（express-as）。"""
        voice = voice_cfg.get("voice") or "en-US-JennyNeural"
        prefs, degree = AZURE_TONES.get(tone, AZURE_TONES[DEFAULT_TONE])
        supported = self._styles_for(voice)
        style = next((st for st in prefs if st in supported), None)
        rate, pitch = TONES.get(tone, TONES[DEFAULT_TONE])
        rate = self._combine(voice_cfg.get("voice_rate"), rate, "%")
        pitch = self._combine(voice_cfg.get("voice_pitch"), pitch, "Hz")
        body = f'<prosody pitch="{pitch}" rate="{rate}">{_xml_escape(text)}</prosody>'
        if style:
            degree = float(voice_cfg.get("style_degree", 1.0)) * degree
            body = f'<mstts:express-as style="{style}" styledegree="{min(degree, 2.0):.2f}">{body}</mstts:express-as>'
        ssml = ('<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" '
                f'xmlns:mstts="https://www.w3.org/2001/mstts" xml:lang="{voice[:5]}">'
                f'<voice name="{voice}">{body}</voice></speak>')
        audio = self._azure_request("/cognitiveservices/v1", data=ssml.encode("utf-8"), headers={
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        })
        with open(out_path, "wb") as f:
            f.write(audio)
        self.last_style = style
        return len(audio) > 0

    def _minimax(self, text, voice_cfg, tone, out_path, lang="zh"):
        """MiniMax 语音：情绪参数 + 台词里的 (laughs)/(sighs) 等音效标签。"""
        key = os.environ.get("MINIMAX_API_KEY")
        if not key:
            raise RuntimeError("没有设置环境变量 MINIMAX_API_KEY")
        voice_id = voice_cfg.get("minimax_voice")
        if not voice_id:
            raise RuntimeError(f"{voice_cfg.get('display_name')} 没设置 minimax_voice")
        emotion, speed_mult = MINIMAX_TONES.get(tone, MINIMAX_TONES[DEFAULT_TONE])
        speed = float(voice_cfg.get("minimax_speed", 1.0)) * speed_mult
        voice_setting = {"voice_id": voice_id, "speed": round(min(max(speed, 0.5), 2.0), 2), "vol": 1,
                         "pitch": int(voice_cfg.get("minimax_pitch", 0))}
        if emotion:
            voice_setting["emotion"] = emotion
        body = {
            "model": voice_cfg.get("minimax_model") or self.minimax_model,
            "text": text, "stream": False, "output_format": "hex",
            "voice_setting": voice_setting,
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
            "language_boost": "Chinese" if lang == "zh" else "English",
        }
        modify = voice_cfg.get("minimax_modify")   # {"pitch": -100~100, "intensity": -100~100, "timbre": -100~100}
        if modify:
            body["voice_modify"] = {k: int(v) for k, v in modify.items() if k in ("pitch", "intensity", "timbre")}
        req = urllib.request.Request(self.minimax_url, data=json.dumps(body).encode("utf-8"), headers={
            "Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        base = data.get("base_resp") or {}
        if base.get("status_code", 0) != 0:
            raise RuntimeError(f"MiniMax 错误 {base.get('status_code')}: {base.get('status_msg')}")
        audio_hex = (data.get("data") or {}).get("audio")
        if not audio_hex:
            raise RuntimeError("MiniMax 没返回音频")
        with open(out_path, "wb") as f:
            f.write(bytes.fromhex(audio_hex))
        self.last_style = emotion or "auto"
        return True

    def _tts(self, text, voice_cfg, tone, base_path):
        """生成语音，返回音频文件路径（.wav 或 .mp3）；失败返回 None。"""
        if not self.tts_enabled:
            return None
        engine = str(voice_cfg.get("tts_engine") or self.engine).lower()   # 每个 AI 可以单独指定引擎
        if engine == "chattts":
            try:
                if self._chattts(text, voice_cfg, tone, base_path + ".wav"):
                    return base_path + ".wav"
            except Exception as e:
                self._warn_once("chattts", f"  ! ChatTTS 失败，改用 edge-tts（{e}）")
        mp3 = base_path + ".mp3"
        if engine == "minimax":
            try:
                if self._minimax(text, voice_cfg, tone, mp3, voice_cfg.get("voice_lang", "zh")):
                    return mp3
            except Exception as e:
                self._warn_once("minimax", f"  ! MiniMax 语音失败，改用 edge-tts（{e}）")
        text = strip_sound_tags(text)   # 其他引擎不认音效标签，念出来会很怪
        if engine == "azure":
            try:
                if self._azure(text, voice_cfg, tone, mp3):
                    return mp3
            except Exception as e:
                self._warn_once("azure", f"  ! Azure 语音失败，改用 edge-tts（{e}）")
        if engine == "openai":
            try:
                if self._openai_tts(text, voice_cfg.get("openai_voice"), tone,
                                    voice_cfg.get("voice_style", ""), mp3):
                    return mp3
            except Exception as e:
                self._warn_once("openai", f"  ! OpenAI 语音失败，改用 edge-tts：{e}")
        try:
            return mp3 if self._edge(text, voice_cfg.get("voice") or "en-US-GuyNeural", tone, mp3,
                                     voice_cfg.get("voice_pitch"), voice_cfg.get("voice_rate")) else None
        except Exception as e:
            self._warn_once("edge", f"  ! TTS 失败，只显示字幕：{e}")
            return None

    def _filter_phrases(self, text, voice_cfg):
        """禁用词：narration.banned_phrases 里的词直接删掉（连同紧跟的标点），
        除非这个 AI 的 allowed_phrases 里允许。"""
        if not text:
            return text
        allowed = set((voice_cfg or {}).get("allowed_phrases") or []) if isinstance(voice_cfg, dict) else set()
        for w in self.cfg.get("banned_phrases") or []:
            if w and w not in allowed and w in text:
                # 句首的连标点一起删；句中的保留后面的标点（“看好了家人们！”→“看好了！”）
                text = re.sub(re.escape(w) + r"([，,！!。.～~、]?)\s*",
                              lambda m: "" if m.start() == 0 else m.group(1), text)
        text = re.sub(r"([，,、])\s*[，,、]+", r"\1", text).strip(" ，,")
        return text or "……"

    # ---------- 主接口 ----------
    def say(self, *args, **kwargs):
        with self.lock:
            return self._say(*args, **kwargs)

    def _say(self, agent, character, color, say_en, say_zh, voice_lang, voice_cfg,
             action=None, spell=None, target=None, tone=None):
        """显示字幕、播放语音、写时间轴。返回建议等待的秒数（等说完再执行动作）。
        voice_cfg: 这个 AI 的配置（读 voice / openai_voice / voice_style）。"""
        self.counter += 1
        say_zh = self._filter_phrases(say_zh, voice_cfg)
        voice_lang = "zh" if voice_lang == "zh" else "en"
        spoken = say_zh if voice_lang == "zh" else say_en
        tone = tone if tone in TONES else DEFAULT_TONE
        shown_en, shown_zh = strip_sound_tags(say_en), strip_sound_tags(say_zh)
        if isinstance(voice_cfg, str):
            voice_cfg = {"voice": voice_cfg}

        audio_path = self._tts(spoken, voice_cfg or {}, tone,
                               os.path.join(self.audio_dir, f"{int(self.start)}_{self.counter}"))
        has_audio = audio_path is not None
        audio_name = os.path.basename(audio_path) if has_audio else None

        duration = None
        if has_audio:
            duration = wav_duration(audio_path) if audio_path.endswith(".wav") else mp3_duration(audio_path)
        if duration is None:
            duration = estimate_speech_seconds(spoken, voice_lang)
        duration += 0.3

        # 上一句还没说完就先等着，避免两段语音叠在一起（动作也会跟着这句话一起出现）
        gap = self.busy_until - time.time()
        if gap > 0:
            time.sleep(gap)
        self.busy_until = time.time() + duration + 0.4

        t = round(time.time() - self.start, 2)
        self._write_current({
            "id": self.counter,
            "agent": agent,
            "character": character,
            "color": color,
            "primary_lang": voice_lang,
            "en": shown_en,
            "zh": shown_zh,
            "spell": spell,
            "target": target,
            "audio": f"audio/{audio_name}" if has_audio else None,
            "hold": max(self.hold, duration + 2),
        })

        with open(self.timeline_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "t": t, "duration": round(duration, 2), "wall": datetime.now().isoformat(timespec="seconds"),
                "agent": agent, "character": character, "action": action, "spell": spell, "target": target,
                "voice_lang": voice_lang, "tone": tone, "en": shown_en, "zh": shown_zh,
            }, ensure_ascii=False) + "\n")

        if not self.wait_for_speech:
            return 0
        return min(duration, self.max_wait)
