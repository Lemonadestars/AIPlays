"""AI 适配层：所有平台共用同一个接口 complete(system, user, ctx) -> 文本。

新增平台只需要在这里加一个类，然后在 config.yaml 里写 provider 名字。
DeepSeek、Gemini 都提供兼容 OpenAI 的接口，所以用 OpenAICompatAgent 就行。
"""

import json
import os
import random
import re


class Agent:
    def __init__(self, name, cfg):
        self.name = name
        self.cfg = cfg
        self.model = cfg.get("model", "")
        self.max_tokens = int(cfg.get("max_tokens", 600))
        self.temperature = cfg.get("temperature")

    def complete(self, system, user, ctx=None):
        raise NotImplementedError

    def _api_key(self, default_env):
        env = self.cfg.get("api_key_env", default_env)
        key = os.environ.get(env)
        if not key:
            raise RuntimeError(f"[{self.name}] 没找到环境变量 {env}，请先设置 API key")
        return key


class AnthropicAgent(Agent):
    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        import anthropic  # 按需导入，没装也不影响别的平台

        self.client = anthropic.Anthropic(api_key=self._api_key("ANTHROPIC_API_KEY"))

    def complete(self, system, user, ctx=None):
        kwargs = dict(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        resp = self.client.messages.create(**kwargs)
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


class OpenAICompatAgent(Agent):
    """OpenAI 官方，以及所有兼容 OpenAI 接口的平台（DeepSeek、Gemini 等）。"""

    def __init__(self, name, cfg):
        super().__init__(name, cfg)
        from openai import OpenAI

        self.client = OpenAI(
            api_key=self._api_key("OPENAI_API_KEY"),
            base_url=cfg.get("base_url") or None,
        )
        # OpenAI 新模型用 max_completion_tokens，多数兼容平台用 max_tokens
        default_param = "max_completion_tokens" if cfg.get("provider") == "openai" else "max_tokens"
        self.token_param = cfg.get("token_param", default_param)
        # 推理模型的“思考强度”（low 最快），以及各平台的私有参数（比如 DeepSeek 关闭思考模式）
        self.reasoning_effort = cfg.get("reasoning_effort")
        self.extra_body = cfg.get("extra_body") or None
        self.fallbacks = [str(m) for m in (cfg.get("fallback_models") or [])]   # 型号用不了时依次换

    def complete(self, system, user, ctx=None):
        kwargs = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            self.token_param: self.max_tokens,
        }
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        while True:
            try:
                resp = self.client.chat.completions.create(**kwargs)
                return resp.choices[0].message.content or ""
            except Exception as e:
                msg = str(e).lower()
                # 这个模型不支持推理强度参数：去掉再试（以后都不再发）
                if "reasoning" in msg and "reasoning_effort" in kwargs:
                    print(f"  ! [{self.name}] 模型不接受 reasoning_effort，去掉后重试")
                    self.reasoning_effort = None
                    kwargs.pop("reasoning_effort")
                    continue
                # 型号不存在 / 下线了：换 fallback_models 里的下一个（以后都用它）
                if self.fallbacks and any(k in msg for k in ("model", "not found", "404", "does not exist")):
                    old, self.model = self.model, self.fallbacks.pop(0)
                    print(f"  ! [{self.name}] 型号 {old} 用不了（{str(e)[:80]}），改用 {self.model}")
                    kwargs["model"] = self.model
                    continue
                raise


class MockAgent(Agent):
    """不调用任何 API，随机选一个合法动作。用来测试整条链路、不花钱。"""

    def complete(self, system, user, ctx=None):
        ctx = ctx or {}
        if ctx.get("roll"):
            return json.dumps({"say_en": "Come on, dice!", "say_zh": "骰子争气点！", "tone": "tense",
                               "reroll": True}, ensure_ascii=False)
        if ctx.get("levelup"):
            return json.dumps({"plan_zh": "职业：圣武士 2；战斗风格已选", "plan_en": "Paladin 2",
                               "say_zh": "升级啦！", "say_en": "Level up!", "tone": "excited"}, ensure_ascii=False)
        if ctx.get("memory"):
            return json.dumps({"memories": [{"text": "测试记忆：大家第一次见到了莱埃泽尔。", "about": ["莱埃泽尔"]}]},
                              ensure_ascii=False)
        if ctx.get("dialog"):
            n = random.randint(1, max(1, ctx.get("options", 1)))
            return json.dumps({"choice": n, "pick": n, "say_en": f"Option {n}, obviously.",
                               "say_zh": f"当然选 {n}。", "tone": "playful"}, ensure_ascii=False)
        if ctx.get("reaction"):
            return json.dumps(random.choice([
                {"say_en": "Nice hit!", "say_zh": "打得漂亮！", "tone": "excited"},
                {"say_en": "You call that a swing?", "say_zh": "你管那叫挥刀？", "tone": "sarcastic"},
            ]), ensure_ascii=False)
        enemies = [u["label"] for u in ctx.get("units", []) if u.get("enemy") and not u.get("dead")]
        spells = ctx.get("spells", [])
        attack = next((s for s in spells if "Attack" in s), spells[0] if spells else None)
        if ctx.get("actions_taken", 0) >= 2 or not enemies or not attack:
            return json.dumps({
                "action": "end_turn",
                "say_en": "That's all I've got this turn.",
                "say_zh": "这回合就到这儿了。",
            })
        target = random.choice(enemies)
        return "Sure! ```json\n" + json.dumps({
            "action": "use_spell",
            "spell": attack,
            "target": target,
            "say_en": f"Hitting {target} because it looks squishy.",
            "say_zh": f"打 {target}，它看起来最脆。",
        }, ensure_ascii=False) + "\n```"


PROVIDERS = {
    "anthropic": AnthropicAgent,
    "openai": OpenAICompatAgent,
    "openai_compatible": OpenAICompatAgent,
    "mock": MockAgent,
}


def make_agent(name, cfg):
    provider = cfg.get("provider")
    if provider not in PROVIDERS:
        raise ValueError(f"[{name}] 未知的 provider: {provider}，可选: {', '.join(PROVIDERS)}")
    return PROVIDERS[provider](name, cfg)


def extract_json(text):
    """从模型回复里抠出第一个 JSON 对象（兼容 ```json 代码块和前后废话）。"""
    if not text:
        raise ValueError("模型返回了空内容")
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("回复里没有 JSON")
    return json.loads(m.group(0))
