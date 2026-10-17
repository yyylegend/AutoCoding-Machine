"""计数来源、工具开销和窗口查询的回归测试；不访问真实模型。"""

from dataclasses import replace
from unittest.mock import Mock

import pytest
import tiktoken

from auto_coding_machine.common import token_utils
from auto_coding_machine.common.llm_client import fetch_model_context_window
from auto_coding_machine.config.settings import settings
from auto_coding_machine.profiles.config import load_profile
from auto_coding_machine.runtime import context


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
    monkeypatch.setattr("auto_coding_machine.common.llm_client.requests.get", lambda *a, **k: response)
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

    monkeypatch.setattr("auto_coding_machine.common.llm_client.requests.get", get)
    assert fetch_model_context_window(base_url="http://localhost:8080/v1/", model="qwen-local") == 8192
    assert requests[-1][0] == "http://localhost:8080/props"
    assert requests[-1][1]["params"] == {"model": "qwen-local"}


def test_other_model_metadata_is_not_used(monkeypatch):
    response = Mock(ok=True)
    response.json.return_value = {"data": [{"id": "other", "context_length": 1000000}]}
    monkeypatch.setattr("auto_coding_machine.common.llm_client.requests.get", lambda *a, **k: response)
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


def _usage_adapter(monkeypatch, usages):
    """返回 Textual 模型 adapter；每次调用依次报告给定的 usage。"""
    from auto_coding_machine.profiles.coding.tui import model as tui_model
    from auto_coding_machine.profiles.coding.tui.model import TuiModelAdapter

    remaining = list(usages)
    monkeypatch.setattr(tui_model, "chat_stream", lambda *args, **kwargs: {
        "content": "完成", "tool_calls": [], "usage": remaining.pop(0),
    })
    adapter = TuiModelAdapter([], lambda _kind, _value: None, session_id="usage")
    return adapter


def test_missing_usage_is_not_reported_as_zero(monkeypatch):
    adapter = _usage_adapter(monkeypatch, [
        {"prompt_tokens": 12, "completion_tokens": 8}, {}, {"prompt_tokens": 0, "completion_tokens": 0},
    ])
    adapter.call([])
    assert adapter.last_prompt_tokens == 12
    adapter.call([])
    assert adapter.last_prompt_tokens is None
    assert not adapter.usage_complete
    assert adapter.prompt_tokens == 12
    adapter.reset_metrics()
    adapter.call([])
    assert adapter.last_prompt_tokens == 0
    assert adapter.usage_complete


def test_cost_and_status_report_missing_usage_and_window_source(tmp_path, monkeypatch):
    import asyncio
    from auto_coding_machine.profiles.config import Profile
    from auto_coding_machine.profiles.coding.tui.app import CodingApp
    from auto_coding_machine.runtime import factory
    from tests.helpers import ready

    monkeypatch.setattr(factory, "resolve_context_info", lambda _model: {"window": 1000000, "source": "手动配置"})
    profile = Profile(skills=(), load_instructions=False)

    async def scenario():
        app = CodingApp(tmp_path, profile)
        async with app.run_test(size=(120, 40)) as pilot:
            await ready(app, pilot)
            app.model.usage_complete = False
            app.model.last_prompt_tokens = 16
            await app.submit("/cost")
            await app.submit("/status")
            await pilot.pause()
            return [str(widget.content) for widget in app.query(".notice")]

    cost, status = asyncio.run(scenario())[-2:]
    assert "本次任务" in cost and "服务端用量报告不完整" in cost
    assert "1000000" in status and "手动配置" in status
    assert "上次服务端输入：16" in status


def test_llama_props_failure_does_not_use_training_window(monkeypatch):
    models = Mock(ok=True)
    models.json.return_value = {"data": [{"id": "qwen", "meta": {"n_ctx_train": 131072}}]}
    props = Mock(ok=False)
    monkeypatch.setattr("auto_coding_machine.common.llm_client.requests.get", lambda url, **kw: props if url.endswith('/props') else models)
    assert fetch_model_context_window(base_url="http://localhost:8080/v1", model="qwen") is None
