"""计数来源、工具开销和窗口查询的回归测试；不访问真实模型。"""

from dataclasses import replace
from unittest.mock import Mock

import pytest
import tiktoken

from src.common import token_utils
from src.common.llm_client import fetch_model_context_window
from src.config.settings import settings
from src.profiles.config import load_profile
from src.runtime import context


@pytest.fixture(autouse=True)
def restore_default_model(monkeypatch):
    monkeypatch.setattr(token_utils, "_DEFAULT_MODEL", None)


def test_known_model_uses_official_encoding_without_init_overwrite():
    text = "你好，this is a tokenizer check"
    token_utils.init_tokenizer("gpt-4o")
    expected = len(tiktoken.encoding_for_model("gpt-4o").encode(text))
    assert token_utils.estimate_token_length(text, "gpt-4o") == expected
    token_utils.init_tokenizer("unknown-local-model")
    assert token_utils.estimate_token_length(text, "gpt-4o") == expected


def test_special_token_text_is_counted_as_user_text():
    assert token_utils.estimate_token_length("<|endoftext|>", "gpt-4") > 0


def test_request_estimate_includes_tools_and_message_metadata():
    messages = [{"role": "user", "content": "你好"}]
    tools = [{"type": "function", "function": {"name": "read_file", "description": "文件说明 " * 100}}]
    assert token_utils.get_token_count(messages, "gpt-4o", tools=tools) > token_utils.get_token_count(messages, "gpt-4o")
    assert token_utils.count_tokens_in_message({"role": "tool", "content": "", "tool_call_id": "call_123"}) > 0


def test_context_manager_keeps_its_model_and_counts_tool_overhead(monkeypatch):
    monkeypatch.setattr(settings, "CONTEXT_SUMMARY_ENABLED", False)
    tools = [{"type": "function", "function": {"name": "example", "description": "hello " * 100}}]
    messages = [{"role": "user", "content": "hello world " * 8} for _ in range(8)]
    # 消息本身没超预算，只有加上工具定义后才需要压缩。
    budget = token_utils.get_token_count(messages, "gpt-4o") + 10
    manager = context.build_context_manager(token_budget=budget, model="gpt-4o", tools=tools)
    assert manager.count_request_tokens(messages) == token_utils.get_token_count(messages, "gpt-4o", tools=tools)
    token_utils.init_tokenizer("gpt-4")
    assert manager.count_request_tokens(messages) == token_utils.get_token_count(messages, "gpt-4o", tools=tools)
    assert len(manager.maybe_compact(messages)) < len(messages)


@pytest.mark.parametrize("value", [True, 1.5, "1.5", -1, 0, None])
def test_provider_rejects_invalid_window(monkeypatch, value):
    response = Mock(ok=True)
    response.json.return_value = {"data": [{"id": "target", "context_length": value}]}
    monkeypatch.setattr("src.common.llm_client.requests.get", lambda *a, **k: response)
    assert fetch_model_context_window(base_url="https://example.com/v1/", model="target") is None


def test_llama_uses_deployed_context_not_training_window(monkeypatch):
    models = Mock(ok=True)
    models.json.return_value = {"data": [{"id": "qwen-local", "meta": {"n_ctx_train": 131072}}]}
    props = Mock(ok=True)
    props.json.return_value = {"default_generation_settings": {"n_ctx": 8192}}
    requests = []

    def get(url, **kwargs):
        requests.append((url, kwargs))
        return props if url.endswith("/props") else models

    monkeypatch.setattr("src.common.llm_client.requests.get", get)
    assert fetch_model_context_window(base_url="http://localhost:8080/v1/", model="qwen-local") == 8192
    assert requests[-1][0] == "http://localhost:8080/props"
    assert requests[-1][1]["params"] == {"model": "qwen-local"}


def test_other_model_metadata_is_not_used(monkeypatch):
    response = Mock(ok=True)
    response.json.return_value = {"data": [{"id": "other", "context_length": 1000000}]}
    monkeypatch.setattr("src.common.llm_client.requests.get", lambda *a, **k: response)
    assert fetch_model_context_window(model="target") is None


def test_window_source_and_profile_override(monkeypatch):
    monkeypatch.setattr(settings, "CODING_CONTEXT_LENGTH", 1000000)
    monkeypatch.setattr(settings, "CODING_LLM_MODEL", "default-model")
    fetch = Mock(return_value=None)
    monkeypatch.setattr(context, "fetch_model_context_window", fetch)
    assert context.resolve_context_info() == {"window": 1000000, "source": "手动配置"}
    fetch.assert_not_called()
    assert context.resolve_context_info("other-model") == {"window": None, "source": "未知"}
    profile = replace(load_profile("review"), model="other-model", context_budget=12000)
    assert context.profile_token_budget(profile) == 12000


def test_window_refresh_does_not_reuse_old_model_settings(monkeypatch):
    monkeypatch.setattr(settings, "CODING_CONTEXT_LENGTH", 64000)
    monkeypatch.setattr(settings, "CODING_LLM_MAX_TOKENS", 4096)
    assert context.resolve_token_budget() == 51200
    monkeypatch.setattr(settings, "CODING_CONTEXT_LENGTH", 32000)
    assert context.resolve_token_budget() == 25600


def test_missing_usage_is_not_reported_as_zero():
    from src.profiles.coding.llm_adapter import StreamingAdapter

    adapter = StreamingAdapter([], Mock(), {})
    adapter._update_metrics({"usage": {"prompt_tokens": 12, "completion_tokens": 8}})
    assert adapter.last_prompt_tokens == 12
    adapter._update_metrics({"usage": {}})
    assert adapter.last_prompt_tokens is None
    assert not adapter.usage_complete
    assert adapter.total_prompt_tokens == 12
    adapter.reset_metrics()
    adapter._update_metrics({"usage": {"prompt_tokens": 0, "completion_tokens": 0}})
    assert adapter.last_prompt_tokens == 0
    assert adapter.usage_complete


def test_missing_usage_is_visible_in_cost():
    from src.profiles.coding.commands.cost import handle_cost
    from src.profiles.coding.llm_adapter import StreamingAdapter

    console = Mock()
    adapter = StreamingAdapter([], console, {})
    adapter._update_metrics({})
    handle_cost({"console": console, "theme": {"dim": "dim"}, "llm": adapter})
    output = str(console.print.call_args_list)
    assert "本次任务" in output
    assert "总消耗未知" in output


def test_status_distinguishes_window_source_and_last_request():
    from io import StringIO
    from types import SimpleNamespace
    from rich.console import Console
    from src.profiles.coding.commands.status import handle_status
    from src.profiles.coding.llm_adapter import StreamingAdapter

    output = StringIO()
    console = Console(file=output, width=160, color_system=None)
    adapter = StreamingAdapter([], console, {}, model="unknown-model")
    adapter._update_metrics({"usage": {"prompt_tokens": 16, "completion_tokens": 4}})
    handle_status({
        "messages": [{"role": "user", "content": "hello"}], "token_budget": 800000,
        "console": console, "theme": {"dim": "dim"}, "llm": adapter,
        "store": SimpleNamespace(session_id="test"), "history": [],
        "tools": SimpleNamespace(get_schemas=lambda: []), "plan_mode": False,
        "context_info": {"window": 1000000, "source": "手动配置"},
    })
    text = output.getvalue()
    assert "1,000,000（手动配置）" in text
    assert "通用回退" in text
    assert "上次请求输入" in text and "16" in text
    assert "当前输入估算" in text


def test_llama_props_failure_does_not_use_training_window(monkeypatch):
    models = Mock(ok=True)
    models.json.return_value = {"data": [{"id": "qwen", "meta": {"n_ctx_train": 131072}}]}
    props = Mock(ok=False)
    monkeypatch.setattr("src.common.llm_client.requests.get", lambda url, **kw: props if url.endswith('/props') else models)
    assert fetch_model_context_window(base_url="http://localhost:8080/v1", model="qwen") is None
