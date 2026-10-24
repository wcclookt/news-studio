"""Editable provider presets, shared by the desktop editor and HTTP adapters.

Model IDs are examples from each provider's own documentation, not a closed list
or a promise that an account has access to them. Endpoints and model IDs remain
ordinary editable configuration values. Documentation checked 2026-10-17.
"""

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ProviderPreset:
    id: str
    label: str
    adapter: str
    base_url: str
    models: tuple[str, ...]
    key_required: bool = True
    hint: str = "模型为示例，可直接输入账户支持的任意模型名称。"
    documentation_url: str = ""


ADAPTER_CHOICES = (
    ("OpenAI 兼容 · Chat Completions", "openai-compatible"),
    ("OpenAI · Responses", "openai-responses"),
    ("Anthropic · Messages", "anthropic"),
)

PROVIDER_PRESETS = (
    ProviderPreset("openai", "OpenAI · GPT", "openai-compatible",
                   "https://api.openai.com/v1", ("gpt-4.1", "gpt-4.1-mini"),
                   documentation_url="https://developers.openai.com/api/docs/models/gpt-4.1"),
    ProviderPreset("anthropic", "Anthropic · Claude", "anthropic",
                   "https://api.anthropic.com/v1", ("claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"),
                   hint="模型 ID 可自定义；推理模型建议将高级设置中的 Token 上限设为至少 8192。",
                   documentation_url="https://platform.claude.com/docs/en/models/overview"),
    ProviderPreset("gemini", "Google · Gemini", "openai-compatible",
                   "https://generativelanguage.googleapis.com/v1beta/openai", ("gemini-3.8-flash",),
                   hint="使用 Google 的 OpenAI 兼容端点；推理模型建议 Token 上限至少 8192。",
                   documentation_url="https://ai.google.dev/gemini-api/docs/openai"),
    ProviderPreset("deepseek", "DeepSeek · 深度求索", "openai-compatible",
                   "https://api.deepseek.com", ("deepseek-flash", "deepseek-v4-pro"),
                   hint="模型 ID 可自定义；默认思考模式会消耗输出额度，建议 Token 上限至少 8192。",
                   documentation_url="https://api-docs.deepseek.com/"),
    ProviderPreset("qwen", "阿里云 · 通义千问", "openai-compatible",
                   "https://dashscope.aliyuncs.com/compatible-mode/v1", ("qwen-plus", "qwen-max", "qwen3.8-max"),
                   hint="中国内地传统端点；其他地域或工作空间请按控制台修改地址和模型。",
                   documentation_url="https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope"),
    ProviderPreset("zhipu", "智谱 · GLM", "openai-compatible",
                   "https://open.bigmodel.cn/api/paas/v4", ("glm-5.3-flash", "glm-5.3"),
                   hint="通用 API 端点；推理模型建议 Token 上限至少 8192。Coding 套餐请核对专用地址。",
                   documentation_url="https://docs.bigmodel.cn/cn/api/introduction"),
    ProviderPreset("moonshot", "月之暗面 · Kimi", "openai-compatible",
                   "https://api.moonshot.ai/v1", ("kimi-k3", "kimi-k2.6"),
                   hint="国际端点；国内账户可改为 https://api.moonshot.cn/v1。推理模型建议 Token 上限至少 8192。",
                   documentation_url="https://platform.kimi.ai/docs/overview"),
    ProviderPreset("minimax", "MiniMax", "anthropic",
                   "https://api.minimax.io/anthropic/v1", ("MiniMax-M3", "MiniMax-M2.7", "MiniMax-M2.5"),
                   hint="使用 Anthropic Messages 协议；思考内容计入输出额度，建议 Token 上限至少 8192。",
                   documentation_url="https://platform.minimax.io/docs/api-reference/text-anthropic-api"),
    ProviderPreset("xai", "xAI · Grok", "openai-compatible",
                   "https://api.x.ai/v1", ("grok-4.5", "grok-4.20-non-reasoning"),
                   documentation_url="https://docs.x.ai/docs/api-reference"),
    ProviderPreset("doubao", "火山引擎 · 豆包", "openai-compatible",
                   "https://ark.cn-beijing.volces.com/api/v3", (),
                   hint="在模型名称中填写火山方舟控制台提供的模型 ID 或推理接入点 ID。",
                   documentation_url="https://www.volcengine.com/docs/82379/1399008"),
    ProviderPreset("nvidia", "NVIDIA · NIM", "openai-compatible",
                   "https://integrate.api.nvidia.com/v1", ("nvidia/llama-3.1-nemotron-70b-instruct", "moonshotai/kimi-k2.6", "z-ai/glm-5.3-flash"),
                   hint="模型来自 NVIDIA 模型列表；推理模型建议 Token 上限至少 8192，可自行替换 ID。",
                   documentation_url="https://integrate.api.nvidia.com/v1/models"),
    ProviderPreset("siliconflow", "硅基流动 · SiliconFlow", "openai-compatible",
                   "https://api.siliconflow.cn/v1", ("Pro/deepseek-ai/DeepSeek-R1",),
                   documentation_url="https://docs.siliconflow.cn/en/userguide/quickstart"),
    ProviderPreset("openrouter", "OpenRouter · 多模型聚合", "openai-compatible",
                   "https://openrouter.ai/api/v1", ("openai/gpt-4.1", "anthropic/claude-sonnet-5.5"),
                   hint="模型 ID 包含服务商前缀，请按 OpenRouter 模型列表填写。",
                   documentation_url="https://openrouter.ai/docs/quickstart"),
    ProviderPreset("groq", "Groq · Llama / 开源模型", "openai-compatible",
                   "https://api.groq.com/openai/v1", ("llama-3.3-70b-versatile", "llama-3.1-8b-instant", "openai/gpt-oss-120b"),
                   documentation_url="https://console.groq.com/docs/models"),
    ProviderPreset("ollama", "Ollama · 本地模型", "openai-compatible",
                   "http://127.0.0.1:11434/v1", ("qwen3", "llama3.2", "gpt-oss"), False,
                   "请先在 Ollama 下载并启动所填模型；默认本地服务不需要 API Key。",
                   "https://docs.ollama.com/openai"),
    ProviderPreset("lmstudio", "LM Studio · 本地模型", "openai-compatible",
                   "http://127.0.0.1:1234/v1", (), False,
                   "先启动 LM Studio 本地服务器，填写已加载的模型标识；开启鉴权时请勾选需要 Key。",
                   "https://lmstudio.ai/docs/developer/openai-compat"),
    ProviderPreset("agnes", "Agnes · 现有兼容服务", "openai-compatible", "", (),
                   hint="保留原有 Agnes 配置；按所用服务填写兼容 API 地址和模型。"),
    ProviderPreset("custom", "自定义 · 兼容 API / 小众模型", "openai-compatible", "", (),
                   hint="选择服务支持的协议，自行填写 API 基础地址、模型名称和鉴权方式。"),
)

PROVIDERS_BY_ID = {preset.id: preset for preset in PROVIDER_PRESETS}

_ADAPTER_ALIASES = {
    "openai": "openai-compatible", "chat-completions": "openai-compatible",
    "openai_compatible": "openai-compatible", "responses": "openai-responses",
    "claude": "anthropic", "messages": "anthropic",
}


def get_provider_preset(provider_id):
    return PROVIDERS_BY_ID.get(str(provider_id).strip().lower())


def resolve_adapter(cfg):
    """Resolve explicit protocol first; unknown providers require a protocol."""
    adapter = str(cfg.get("adapter") or "").strip().lower()
    if adapter:
        adapter = _ADAPTER_ALIASES.get(adapter, adapter)
        if adapter not in {value for _, value in ADAPTER_CHOICES}:
            raise ValueError("未知 AI 适配协议，请选择 OpenAI 兼容、Responses 或 Anthropic")
        return adapter
    provider = str(cfg.get("provider") or "agnes").strip().lower()
    preset = get_provider_preset(provider)
    if preset:
        return preset.adapter
    if provider in _ADAPTER_ALIASES:
        return _ADAPTER_ALIASES[provider]
    if provider in {value for _, value in ADAPTER_CHOICES}:
        return provider
    raise ValueError("未知 AI 服务商，请选择预设或为自定义服务指定适配协议")


def provider_requires_api_key(cfg):
    if "requires_api_key" in cfg:
        return cfg["requires_api_key"] is not False
    preset = get_provider_preset(cfg.get("provider", "agnes"))
    return preset.key_required if preset else True


def provider_is_configured(cfg):
    """Empty optional slots stay inactive; keyless local services can run."""
    has_value = lambda key: bool(str(cfg.get(key) or "").strip())
    return has_value("api_key") or (
        not provider_requires_api_key(cfg) and has_value("base_url") and has_value("model")
    )


def infer_provider_id(cfg):
    """Suggest a preset without changing any saved endpoint/model values."""
    provider = str(cfg.get("provider") or "").strip().lower()
    if provider in PROVIDERS_BY_ID and provider != "agnes":
        return provider
    try:
        parsed = urlparse(str(cfg.get("base_url") or ""))
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return provider if provider in PROVIDERS_BY_ID else "custom"
    for preset in PROVIDER_PRESETS:
        if preset.base_url and host and urlparse(preset.base_url).hostname == host:
            if host in ("127.0.0.1", "localhost"):
                if port != urlparse(preset.base_url).port:
                    continue
            return preset.id
    return provider if provider in PROVIDERS_BY_ID else "custom"
