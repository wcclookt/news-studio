"""Protocol and failover regressions; HTTP calls are always mocked."""

import unittest
from unittest.mock import Mock, patch

import requests

from news_agent.config import Config
from news_agent.llm import (
    AgnesProvider, AnthropicProvider, LLMClient, LLMError,
    OpenAICompatibleProvider, OpenAIResponsesProvider, ZhipuProvider,
)
from news_agent.model_catalog import (
    PROVIDER_PRESETS, get_provider_preset, infer_provider_id, provider_is_configured,
    provider_requires_api_key, resolve_adapter,
)


def model_config(**changes):
    return {"provider": "custom", "api_key": "unit-test-key",
            "base_url": "https://model.example.test/v1/", "model": "small-model",
            "retries": 0, **changes}


def response(data, status=200):
    return Mock(status_code=status, json=Mock(return_value=data))


class PresetTests(unittest.TestCase):
    def test_major_providers_have_known_adapter_and_custom_models_are_allowed(self):
        self.assertEqual(len({p.id for p in PROVIDER_PRESETS}), len(PROVIDER_PRESETS))
        for preset in PROVIDER_PRESETS:
            with self.subTest(preset=preset.id):
                self.assertEqual(resolve_adapter({"provider": preset.id}), preset.adapter)
        self.assertEqual(resolve_adapter({"provider": "my-private-provider", "adapter": "anthropic"}), "anthropic")
        self.assertEqual(resolve_adapter({"provider": "agnes"}), "openai-compatible")
        self.assertEqual(resolve_adapter({"provider": "zhipu"}), "openai-compatible")
        self.assertFalse(get_provider_preset("ollama").key_required)

    def test_unknown_provider_and_unknown_explicit_adapter_are_rejected(self):
        with self.assertRaises(ValueError):
            resolve_adapter({"provider": "mistyped-provider"})
        with self.assertRaises(ValueError):
            resolve_adapter({"provider": "openai", "adapter": "mistyped-adapter"})

    def test_local_provider_can_run_without_key_but_empty_slot_stays_disabled(self):
        self.assertFalse(provider_requires_api_key({"provider": "ollama"}))
        self.assertFalse(provider_is_configured({"provider": "ollama", "api_key": ""}))
        self.assertTrue(provider_is_configured(model_config(provider="ollama", api_key="")))
        self.assertFalse(provider_is_configured(model_config(api_key="")))
        self.assertTrue(provider_is_configured(model_config(api_key="", requires_api_key=False)))
        self.assertFalse(provider_is_configured(model_config(api_key="   ")))

    def test_provider_inference_handles_invalid_url_for_editor_repair(self):
        self.assertEqual(infer_provider_id({"base_url": "http://[broken"}), "custom")
        self.assertEqual(infer_provider_id({"base_url": "http://localhost:bad/v1"}), "custom")


class ProtocolTests(unittest.TestCase):
    @patch("news_agent.llm.requests.post")
    def test_compatible_request_and_array_response(self, post):
        post.return_value = response({"choices": [{"message": {"content": [
            {"type": "text", "text": "第一段"}, {"type": "text", "text": "第二段"},
        ]}}]})
        actual = OpenAICompatibleProvider(model_config(model="niche/model:quantized")).complete("system", "user")
        self.assertEqual(actual, "第一段\n第二段")
        args, kwargs = post.call_args
        self.assertEqual(args, ("https://model.example.test/v1/chat/completions",))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer unit-test-key")
        self.assertEqual(kwargs["json"]["model"], "niche/model:quantized")
        self.assertEqual(kwargs["json"]["max_tokens"], 2048)
        self.assertEqual(kwargs["json"]["messages"][0], {"role": "system", "content": "system"})

    @patch("news_agent.llm.requests.post")
    def test_current_openai_reasoning_model_uses_supported_token_parameter(self, post):
        post.return_value = response({"choices": [{"message": {"content": "ok"}}]})
        OpenAICompatibleProvider(model_config(provider="openai", model="gpt-5-mini")).complete("s", "u")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["max_completion_tokens"], 2048)
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("temperature", body)

    @patch("news_agent.llm.requests.post")
    def test_aggregator_reasoning_model_does_not_receive_unsupported_sampling(self, post):
        post.return_value = response({"choices": [{"message": {"content": "ok"}}]})
        OpenAICompatibleProvider(model_config(provider="openrouter", model="openai/gpt-5-mini")).complete("s", "u")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["max_completion_tokens"], 2048)
        self.assertNotIn("temperature", body)

    @patch("news_agent.llm.requests.post")
    def test_anthropic_uses_native_headers_system_and_text_blocks(self, post):
        post.return_value = response({"content": [
            {"type": "thinking", "thinking": "not user-facing"},
            {"type": "text", "text": "正文"},
        ]})
        text = AnthropicProvider(model_config(provider="anthropic")).complete("rules", "question")
        self.assertEqual(text, "正文")
        args, kwargs = post.call_args
        self.assertTrue(args[0].endswith("/v1/messages"))
        self.assertEqual(kwargs["headers"]["x-api-key"], "unit-test-key")
        self.assertEqual(kwargs["headers"]["anthropic-version"], "2023-06-01")
        self.assertNotIn("Authorization", kwargs["headers"])
        self.assertEqual(kwargs["json"]["system"], "rules")
        self.assertEqual(kwargs["json"]["messages"], [{"role": "user", "content": "question"}])

    @patch("news_agent.llm.requests.post")
    def test_responses_extracts_visible_output_and_uses_native_endpoint(self, post):
        post.return_value = response({"output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "answer"}]},
        ]})
        text = OpenAIResponsesProvider(model_config()).complete("rules", "question")
        self.assertEqual(text, "answer")
        self.assertTrue(post.call_args.args[0].endswith("/v1/responses"))
        self.assertEqual(post.call_args.kwargs["json"]["max_output_tokens"], 2048)
        self.assertFalse(post.call_args.kwargs["json"]["store"])

    @patch("news_agent.llm.requests.post")
    def test_keyless_local_request_omits_bearer_header(self, post):
        post.return_value = response({"choices": [{"message": {"content": "local answer"}}]})
        cfg = model_config(provider="ollama", api_key="", base_url="http://127.0.0.1:11434/v1")
        client = LLMClient(Config({"llm": {"primary": cfg}}))
        self.assertEqual(client.complete_primary_only("s", "u"), "local answer")
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])

    @patch("news_agent.llm.requests.post")
    def test_empty_reasoning_only_reply_is_an_error(self, post):
        post.return_value = response({"choices": [{"message": {"content": "", "reasoning_content": "hidden"}}]})
        with self.assertRaisesRegex(LLMError, "没有返回正文"):
            OpenAICompatibleProvider(model_config()).complete("s", "u")

    @patch("news_agent.llm.requests.post")
    def test_full_endpoint_does_not_get_a_duplicate_suffix(self, post):
        post.return_value = response({"choices": [{"message": {"content": "ok"}}]})
        OpenAICompatibleProvider(model_config(base_url="https://model.example.test/v1/chat/completions")).complete("s", "u")
        self.assertEqual(post.call_args.args[0], "https://model.example.test/v1/chat/completions")

    def test_legacy_class_names_and_agnes_default_limit_remain_usable(self):
        self.assertIsInstance(ZhipuProvider(model_config()), OpenAICompatibleProvider)
        self.assertEqual(AgnesProvider(model_config()).max_tokens, 8192)
        self.assertEqual(AgnesProvider(model_config(max_tokens=1234)).max_tokens, 1234)

    def test_bad_endpoint_or_timeout_gives_safe_configuration_error(self):
        for changes in ({"base_url": "http://[broken"}, {"base_url": "http://localhost:not-a-port/v1"},
                        {"timeout": float("nan")}):
            with self.subTest(changes=changes), self.assertRaises(LLMError):
                OpenAICompatibleProvider(model_config(**changes))


class FailoverTests(unittest.TestCase):
    @patch("news_agent.llm.requests.post")
    def test_actual_usage_counts_include_cached_input_and_rejected_empty_reply(self, post):
        post.side_effect = [
            response({"choices": [{"message": {"content": ""}}],
                      "usage": {"prompt_tokens": 100, "completion_tokens": 12,
                                "prompt_tokens_details": {"cached_tokens": 40}}}),
            response({"content": [{"type": "text", "text": "fallback"}],
                      "usage": {"input_tokens": 20, "output_tokens": 10,
                                "cache_read_input_tokens": 30, "cache_creation_input_tokens": 5}}),
        ]
        client = LLMClient(Config({"llm": {"primary": model_config(),
                                          "fallback": model_config(provider="anthropic")}}))
        self.assertEqual(client.complete("s", "u"), "fallback")
        self.assertEqual(client.token_usage, {"input_tokens": 155, "output_tokens": 22,
                                             "cached_input_tokens": 70, "reported_calls": 2})

    @patch("news_agent.llm.requests.post")
    def test_missing_usage_is_not_invented_and_property_returns_independent_snapshot(self, post):
        post.return_value = response({"choices": [{"message": {"content": "ok"}}]})
        client = LLMClient(Config({"llm": {"primary": model_config()}}))
        client.complete("s", "u")
        snapshot = client.token_usage
        self.assertEqual(snapshot["reported_calls"], 0)
        snapshot["input_tokens"] = 123
        self.assertEqual(client.token_usage["input_tokens"], 0)

    @patch("news_agent.llm.requests.post")
    def test_fallback_still_runs_when_primary_adapter_is_invalid(self, post):
        post.return_value = response({"choices": [{"message": {"content": "fallback"}}]})
        cfg = Config({"llm": {
            "primary": model_config(provider="typo"),
            "fallback": model_config(provider="zhipu"),
        }})
        self.assertEqual(LLMClient(cfg).complete("s", "u"), "fallback")
        post.assert_called_once()

    @patch("news_agent.llm.requests.post")
    def test_disabled_primary_does_not_treat_fallback_as_primary_circuit(self, post):
        post.return_value = response({"choices": [{"message": {"content": "fallback"}}]})
        cfg = Config({"llm": {"primary": {}, "fallback": model_config()}})
        client = LLMClient(cfg)
        client._circuit_open_until = float("inf")
        self.assertEqual(client.complete("s", "u"), "fallback")

    @patch("news_agent.llm.requests.post")
    def test_auth_error_is_not_retried_and_error_body_never_exposes_key(self, post):
        post.return_value = response({"error": "unit-test-key"}, status=401)
        with self.assertRaises(LLMError) as error:
            OpenAICompatibleProvider(model_config(retries=2)).complete("s", "u")
        self.assertIn("401", str(error.exception))
        self.assertNotIn("unit-test-key", str(error.exception))
        post.assert_called_once()

    @patch("news_agent.llm.time.sleep")
    @patch("news_agent.llm.requests.post")
    def test_rate_limit_is_retried(self, post, sleep):
        post.side_effect = [response({}, status=429), response({"choices": [{"message": {"content": "ok"}}]})]
        self.assertEqual(OpenAICompatibleProvider(model_config(retries=1)).complete("s", "u"), "ok")
        self.assertEqual(post.call_count, 2)
        sleep.assert_called_once_with(2)

    @patch("news_agent.llm.requests.post")
    def test_network_exception_does_not_leak_request_url_or_api_key(self, post):
        post.side_effect = requests.ConnectionError("https://host/?key=unit-test-key")
        with self.assertRaises(LLMError) as error:
            OpenAICompatibleProvider(model_config()).complete("s", "u")
        self.assertIn("ConnectionError", str(error.exception))
        self.assertNotIn("unit-test-key", str(error.exception))


if __name__ == "__main__":
    unittest.main()
