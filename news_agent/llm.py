import json
import math
import re
import threading
import time
from urllib.parse import urlparse

import requests

from .logger import get_logger
from .model_catalog import provider_is_configured, provider_requires_api_key, resolve_adapter

log = get_logger()

CATEGORY_NAMES = {
    "ai_model": "AI模型发布",
    "tech_product": "科技新品",
    "github_project": "GitHub开源项目",
    "terms_policy": "条款/政策变更",
    "science": "科技/科学进展",
    "game": "游戏",
    "military": "军事/国防",
    "world": "国际时政",
    "other": "其他",
}


class LLMError(Exception):
    pass


class LLMHTTPError(LLMError):
    def __init__(self, status_code):
        self.status_code = status_code
        self.retryable = status_code in (408, 409, 429) or status_code >= 500
        super().__init__(f"模型 API 返回 HTTP {status_code}，请检查地址、模型权限、额度和 API Key")


class BaseProvider:
    def __init__(self, cfg):
        self.api_key = str(cfg.get("api_key") or "").strip()
        self.model = str(cfg.get("model") or "").strip()
        self.base_url = str(cfg.get("base_url") or "").strip().rstrip("/")
        self.provider_id = str(cfg.get("provider") or "agnes").lower()
        self.last_usage = {}
        self.proxy = cfg.get("proxy") or None
        try:
            self.timeout = float(cfg.get("timeout", 90))
            self.retries = int(cfg.get("retries", 2))
            self.max_tokens = int(cfg.get("max_tokens", 2048))
        except (ValueError, TypeError):
            raise LLMError("模型超时、重试次数和输出 Token 上限必须是数值") from None
        if not math.isfinite(self.timeout) or self.timeout <= 0 or self.retries < 0 or self.max_tokens <= 0:
            raise LLMError("模型超时和输出 Token 上限应大于 0，重试次数不能为负数")
        try:
            parsed = urlparse(self.base_url)
            valid_url = parsed.scheme in ("http", "https") and bool(parsed.hostname)
            if parsed.port is not None:
                valid_url = valid_url and 1 <= parsed.port <= 65535
        except ValueError:
            valid_url = False
        if not valid_url:
            raise LLMError("请填写完整的模型 API 基础地址（http:// 或 https://）")
        if not self.model:
            raise LLMError("请填写模型名称")
        if provider_requires_api_key(cfg) and not self.api_key:
            raise LLMError("该模型服务需要 API Key")

    def _proxies(self):
        if self.proxy:
            return {"http": self.proxy, "https": self.proxy}
        return None

    def complete(self, system_prompt, user_prompt):
        last_error = None
        for attempt in range(self.retries + 1):
            try:
                return self._complete(system_prompt, user_prompt)
            except requests.RequestException as exc:
                # URLs, query strings and provider error bodies may contain keys.
                # Report only the exception category; never log the raw exception.
                last_error = LLMError(f"模型 API 网络请求失败（{type(exc).__name__}）")
            except LLMHTTPError as exc:
                last_error = exc
                if not exc.retryable:
                    raise
            except LLMError:
                raise
            except (ValueError, TypeError, KeyError, IndexError):
                raise LLMError("模型 API 响应格式不正确，请检查适配协议和模型") from None
            log.warning("%s 第%d次调用失败: %s", type(self).__name__, attempt + 1, last_error)
            if attempt < self.retries:
                time.sleep(2 * (attempt + 1))
        raise LLMError(f"模型 API 调用失败：{last_error}") from None

    def _endpoint(self, suffix):
        return self.base_url if self.base_url.endswith(suffix) else self.base_url + suffix

    def _post_json(self, suffix, body, headers):
        resp = requests.post(self._endpoint(suffix), headers=headers, json=body,
                             timeout=self.timeout, proxies=self._proxies())
        if not 200 <= resp.status_code < 300:
            raise LLMHTTPError(resp.status_code)
        try:
            data = resp.json()
        except ValueError:
            raise LLMError("模型 API 未返回 JSON，请检查基础地址和适配协议") from None
        if not isinstance(data, dict):
            raise LLMError("模型 API 响应格式不正确：应返回 JSON 对象")
        self._record_usage(data)
        return data

    def _record_usage(self, data):
        usage = data.get("usage")
        if not isinstance(usage, dict) or not any(key in usage for key in (
            "prompt_tokens", "input_tokens", "completion_tokens", "output_tokens",
            "cache_read_input_tokens", "cache_creation_input_tokens",
        )):
            return
        def count(value):
            return value if type(value) is int and value >= 0 else 0
        cached_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        cached_details = cached_details if isinstance(cached_details, dict) else {}
        input_tokens = count(usage.get("prompt_tokens", usage.get("input_tokens")))
        cached = count(usage.get("cache_read_input_tokens", cached_details.get("cached_tokens")))
        # Anthropic's input_tokens excludes cache reads/writes; OpenAI already
        # includes its cached tokens in the total prompt/input count.
        if "cache_read_input_tokens" in usage or "cache_creation_input_tokens" in usage:
            input_tokens += cached + count(usage.get("cache_creation_input_tokens"))
        self.last_usage = {"input_tokens": input_tokens,
                           "output_tokens": count(usage.get("completion_tokens", usage.get("output_tokens"))),
                           "cached_input_tokens": cached}
        log.info("模型用量[%s] 输入=%d 输出=%d 缓存命中=%d tokens", type(self).__name__,
                 input_tokens, self.last_usage["output_tokens"], cached)

    def _bearer_headers(self):
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _require_text(text):
        if not isinstance(text, str) or not text.strip():
            raise LLMError("模型没有返回正文，请检查模型能力或增大输出 Token 上限")
        return text.strip()


class OpenAICompatibleProvider(BaseProvider):

    def _complete(self, system_prompt, user_prompt):
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        # Current OpenAI reasoning models reject legacy max_tokens. Account
        # for aggregator prefixes (e.g. openai/gpt-5) as well as direct IDs.
        model_id = self.model.rsplit("/", 1)[-1].lower()
        reasoning_model = bool(re.match(r"^(?:gpt-[5-9]|o[134](?:-|$))", model_id))
        if self.provider_id in ("openai", "groq") or reasoning_model:
            body["max_completion_tokens"] = self.max_tokens
        else:
            body["max_tokens"] = self.max_tokens
        # Leave sampling at the service default: several current reasoning
        # families reject temperature or require their own fixed value.
        data = self._post_json("/chat/completions", body, self._bearer_headers())
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("OpenAI 兼容响应中缺少 choices[0].message.content") from None
        if isinstance(content, list):
            content = "\n".join(item["text"] for item in content
                                if isinstance(item, dict) and isinstance(item.get("text"), str))
        return self._require_text(content)


class AnthropicProvider(BaseProvider):
    def _complete(self, system_prompt, user_prompt):
        headers = {"Content-Type": "application/json", "anthropic-version": "2023-06-01"}
        if self.api_key:
            headers["x-api-key"] = self.api_key
        body = {"model": self.model, "max_tokens": self.max_tokens,
                "messages": [{"role": "user", "content": user_prompt}]}
        if system_prompt:
            body["system"] = system_prompt
        data = self._post_json("/messages", body, headers)
        content = data.get("content")
        if not isinstance(content, list):
            raise LLMError("Anthropic 响应中缺少 content 列表")
        text = "\n".join(item["text"] for item in content
                         if isinstance(item, dict) and item.get("type") == "text"
                         and isinstance(item.get("text"), str))
        return self._require_text(text)


class OpenAIResponsesProvider(BaseProvider):
    def _complete(self, system_prompt, user_prompt):
        body = {"model": self.model, "input": user_prompt,
                "max_output_tokens": self.max_tokens, "store": False}
        if system_prompt:
            body["instructions"] = system_prompt
        data = self._post_json("/responses", body, self._bearer_headers())
        if isinstance(data.get("output_text"), str):
            return self._require_text(data["output_text"])
        texts = []
        output = data.get("output")
        if isinstance(output, list):
            for message in output:
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, list):
                    texts.extend(item["text"] for item in content if isinstance(item, dict)
                                 and item.get("type") == "output_text" and isinstance(item.get("text"), str))
        return self._require_text("\n".join(texts))


class ZhipuProvider(OpenAICompatibleProvider):
    """Legacy public class retained for existing callers."""


class AgnesProvider(OpenAICompatibleProvider):
    def __init__(self, cfg):
        super().__init__({"max_tokens": 8192, **cfg})


class LLMClient:
    def __init__(self, cfg, use_chat=False):
        self.cfg = cfg
        self.use_chat = use_chat
        if use_chat:
            self.primary_cfg = cfg.chat_primary()
            self.fallback_cfg = cfg.chat_fallback()
        else:
            self.primary_cfg = cfg.llm_primary()
            self.fallback_cfg = cfg.llm_fallback()
        self._last_provider = None
        self._primary_failures = 0
        self._circuit_open_until = 0
        self._circuit_open_seconds = float(self.primary_cfg.get("circuit_open_seconds", 600))
        self._lock = threading.Lock()
        self._usage_totals = {"input_tokens": 0, "output_tokens": 0,
                              "cached_input_tokens": 0, "reported_calls": 0}
        self._config_signature = json.dumps([self.primary_cfg, self.fallback_cfg], sort_keys=True)
        self._config_generation = 0

    def _current_configuration(self):
        from copy import deepcopy
        # One coherent snapshot per model request; do not change its auth half
        # way through. Later calls/batches use the new configuration.
        cfg = self.cfg.snapshot() if callable(getattr(self.cfg, 'snapshot', None)) else self.cfg
        primary = deepcopy(cfg.chat_primary() if self.use_chat else cfg.llm_primary())
        fallback = deepcopy(cfg.chat_fallback() if self.use_chat else cfg.llm_fallback())
        signature = json.dumps([primary, fallback], sort_keys=True)
        with self._lock:
            if signature != self._config_signature:
                self.primary_cfg, self.fallback_cfg = primary, fallback
                self._config_signature = signature
                self._config_generation += 1
                self._primary_failures = 0
                self._circuit_open_until = 0
                self._circuit_open_seconds = float(primary.get('circuit_open_seconds', 600))
            return primary, fallback, self._config_generation

    def _build_provider(self, pc):
        try:
            adapter = resolve_adapter(pc)
        except ValueError as exc:
            raise LLMError(str(exc)) from None
        if adapter == "anthropic":
            return AnthropicProvider(pc)
        if adapter == "openai-responses":
            return OpenAIResponsesProvider(pc)
        provider = str(pc.get("provider") or "agnes").lower()
        if provider == "zhipu":
            return ZhipuProvider(pc)
        if provider == "agnes":
            return AgnesProvider(pc)
        return OpenAICompatibleProvider(pc)

    def _primary_blocked(self):
        return time.time() < self._circuit_open_until

    def complete(self, system_prompt, user_prompt):
        primary, fallback, generation = self._current_configuration()
        providers = [(True, primary), (False, fallback)]
        providers = [(is_primary, pc) for is_primary, pc in providers if provider_is_configured(pc)]
        if not providers:
            raise LLMError("没有可用的模型配置；请填写 API Key，或配置无需 Key 的本地模型")
        for is_primary, pc in providers:
            with self._lock:
                blocked = is_primary and self._primary_blocked()
            if blocked:
                log.info("主模型熔断中，直接使用备用 provider")
                continue
            try:
                provider = self._build_provider(pc)
                try:
                    text = provider.complete(system_prompt, user_prompt)
                finally:
                    self._accumulate_usage(provider)
                with self._lock:
                    if is_primary and generation == self._config_generation:
                        self._primary_failures = 0
                        self._circuit_open_until = 0
                    self._last_provider = type(provider).__name__
                return text
            except LLMError as e:
                log.warning("降级: %s", e)
                if is_primary:
                    with self._lock:
                        if generation != self._config_generation:
                            continue
                        self._primary_failures += 1
                        if self._primary_failures >= int(primary.get("circuit_failures", 2)):
                            self._circuit_open_until = time.time() + self._circuit_open_seconds
                            log.info("主模型连续失败 %d 次，熔断 %g 秒", self._primary_failures, self._circuit_open_seconds)
                            self._primary_failures = 0
        raise LLMError("所有 LLM provider 均失败")

    def complete_primary_only(self, system_prompt, user_prompt):
        primary, _, generation = self._current_configuration()
        if not provider_is_configured(primary):
            raise LLMError("没有配置可用的主模型")
        provider = self._build_provider(primary)
        try:
            text = provider.complete(system_prompt, user_prompt)
        finally:
            self._accumulate_usage(provider)
        with self._lock:
            if generation == self._config_generation:
                self._primary_failures = 0
                self._circuit_open_until = 0
            self._last_provider = type(provider).__name__
        return text

    @property
    def last_provider(self):
        return self._last_provider

    def _accumulate_usage(self, provider):
        if provider.last_usage:
            with self._lock:
                for key, value in provider.last_usage.items():
                    self._usage_totals[key] += value
                self._usage_totals["reported_calls"] += 1

    @property
    def token_usage(self):
        """Actual counts reported by APIs; unknown usage is never estimated."""
        with self._lock:
            return dict(self._usage_totals)


def parse_json_array(text):
    if not text:
        raise ValueError("LLM 输出为空")
    text = text.strip()
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = text.replace("```", "")
    start = text.find("[")
    if start == -1:
        raise ValueError("未找到 JSON 数组")
    positions = [i for i in range(len(text) - 1, start - 1, -1) if text[i] == "]"]
    for end in positions:
        candidate = text[start : end + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    raise ValueError("JSON 数组解析失败")
