"""Tencent MemoryCore v3 只读 Adapter；测试不连接真实服务。"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest
import requests

from auto_coding_machine.engine import AgentResponse
from auto_coding_machine.memory import (
    MemoryCapture,
    MemoryQuery,
    MemoryScope,
    MemoryWriteError,
    MemoryWriteUncertainError,
    TencentMemoryCoreProvider,
)
from auto_coding_machine.memory.recall import MemoryRecallSelector
from auto_coding_machine.profiles.config import load_profile
from auto_coding_machine.runtime import open_harness_session


def _query(scope=None):
    return MemoryQuery(
        "SQLite backup",
        scope or MemoryScope(user_id="user-1", team_id="team-1", agent_id="agent-1"),
        max_items=2, max_tokens=128, timeout_seconds=1.5,
    )


class FakeResponse:
    def __init__(self, body, status=200):
        self.body = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("gateway error")

    def json(self):
        return self.body


def _item(*, user_id="user-1", team_id="team-1", agent_id="agent-1"):
    return {
        "id": "atomic-7", "content": "SQLite backup is daily",
        "user_id": user_id, "team_id": team_id, "agent_id": agent_id,
    }


def test_tencent_recall_maps_v3_request_and_provenance(monkeypatch):
    monkeypatch.setenv("TDAI_MEMORY_API_KEY", "test-secret")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse({"code": 0, "data": {"items": [_item()]}})

    monkeypatch.setattr(requests, "post", fake_post)
    provider = TencentMemoryCoreProvider("https://memory.example", service_id="space-1")

    hits = provider.recall(_query())

    assert len(calls) == 1
    assert calls[0][0] == "https://memory.example/v3/atomic/search"
    assert calls[0][1]["json"] == {
        "team_id": "team-1", "agent_id": "agent-1", "user_id": "user-1",
        "query": "SQLite backup", "limit": 2,
    }
    assert calls[0][1]["headers"]["Authorization"] == "Bearer test-secret"
    assert calls[0][1]["headers"]["x-tdai-service-id"] == "space-1"
    assert calls[0][1]["timeout"] == 1.5
    assert calls[0][1]["allow_redirects"] is False
    assert [(hit.text, hit.source, hit.scope) for hit in hits] == [
        ("SQLite backup is daily", "tencent:atomic:atomic-7", _query().scope),
    ]


@pytest.mark.parametrize("scope", [
    MemoryScope(user_id="user-1"),
    MemoryScope(user_id="user-1", team_id="team-1"),
    MemoryScope(user_id="user-1", agent_id="agent-1"),
])
def test_tencent_rejects_incomplete_scope_before_network(monkeypatch, scope):
    monkeypatch.setenv("TDAI_MEMORY_API_KEY", "test-secret")
    monkeypatch.setattr(requests, "post", lambda *_args, **_kwargs: pytest.fail("must not call gateway"))
    provider = TencentMemoryCoreProvider("https://memory.example", service_id="space-1")

    with pytest.raises(ValueError, match="team_id.*agent_id|team_id|agent_id"):
        provider.recall(_query(scope))


def test_tencent_rejects_missing_key_and_cleartext_remote_endpoint(monkeypatch):
    monkeypatch.delenv("TDAI_MEMORY_API_KEY", raising=False)
    with pytest.raises(ValueError, match="TDAI_MEMORY_API_KEY"):
        TencentMemoryCoreProvider("https://memory.example", service_id="space-1")
    monkeypatch.setenv("TDAI_MEMORY_API_KEY", "test-secret")
    with pytest.raises(ValueError, match="HTTPS"):
        TencentMemoryCoreProvider("http://memory.example", service_id="space-1")


@pytest.mark.parametrize("body", [
    {"code": 0, "data": {"items": [_item(user_id="other-user")]}},
    {"code": 0, "data": {"items": [_item(team_id="other-team")]}},
    {"code": 0, "data": {"items": [_item(agent_id="other-agent")]}},
    {"code": 0, "data": {"items": [{"content": "no id"}]}},
    {"code": 0, "data": {}},
    {"code": 401, "message": "denied", "data": None},
    {"code": 0, "data": {"items": [_item(user_id=1)]}},
])
def test_tencent_rejects_foreign_or_invalid_response(monkeypatch, body):
    monkeypatch.setenv("TDAI_MEMORY_API_KEY", "test-secret")
    monkeypatch.setattr(requests, "post", lambda *_args, **_kwargs: FakeResponse(body))
    provider = TencentMemoryCoreProvider("https://memory.example", service_id="space-1")

    with pytest.raises(ValueError):
        provider.recall(_query())


def test_tencent_does_not_follow_redirects(monkeypatch):
    monkeypatch.setenv("TDAI_MEMORY_API_KEY", "test-secret")
    monkeypatch.setattr(
        requests, "post",
        lambda *_args, **_kwargs: FakeResponse({"code": 0, "data": {"items": [_item()]}}, status=302),
    )
    provider = TencentMemoryCoreProvider("https://memory.example", service_id="space-1")

    with pytest.raises(ValueError, match="重定向"):
        provider.recall(_query())


def test_tencent_timeout_skips_recall_without_leaking_key_or_writing_jsonl(
    tmp_path, monkeypatch, caplog,
):
    from auto_coding_machine.runtime import factory

    monkeypatch.setenv("TDAI_MEMORY_API_KEY", "test-secret")
    monkeypatch.setattr(factory, "resolve_context_info", lambda _model: {"window": None, "source": "test"})
    monkeypatch.setattr(factory, "profile_token_budget", lambda _profile, **_kwargs: 8000)

    def timeout(_url, **_kwargs):
        raise requests.Timeout("Bearer test-secret")

    monkeypatch.setattr(requests, "post", timeout)
    seen = []

    def model_fn(messages):
        seen.extend(messages)
        return AgentResponse(content="完成", done=True)

    provider = TencentMemoryCoreProvider("https://memory.example", service_id="space-1")
    session = open_harness_session(
        tmp_path, model_fn, profile=load_profile("review"),
        memory_provider=provider, memory_scope=_query().scope,
    )

    assert session.begin_run("SQLite backup").start()["status"] == "success"
    assert "外部记忆召回失败" in caplog.text
    assert "test-secret" not in caplog.text
    assert all("SQLite backup is daily" not in str(message) for message in seen)
    assert [message["role"] for message in session.store.load()] == ["user", "assistant"]


def test_tencent_local_gateway_recall_is_request_only(tmp_path, monkeypatch):
    from auto_coding_machine.runtime import factory

    monkeypatch.setenv("TDAI_MEMORY_API_KEY", "test-secret")
    monkeypatch.setattr(factory, "resolve_context_info", lambda _model: {"window": None, "source": "test"})
    monkeypatch.setattr(factory, "profile_token_budget", lambda _profile, **_kwargs: 8000)
    received = []

    class GatewayHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, body, self.headers.get("x-tdai-service-id")))
            response = json.dumps({"code": 0, "data": {"items": [_item()]}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), GatewayHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        provider = TencentMemoryCoreProvider(
            f"http://127.0.0.1:{server.server_port}", service_id="space-1",
        )
        seen = []

        def model_fn(messages):
            seen.extend(messages)
            return AgentResponse(content="完成", done=True)

        session = open_harness_session(
            tmp_path, model_fn, profile=load_profile("review"),
            memory_provider=provider, memory_scope=_query().scope,
        )
        assert session.begin_run("SQLite backup").start()["status"] == "success"
        assert received[0][0] == "/v3/atomic/search"
        assert received[0][1]["user_id"] == "user-1"
        assert received[0][2] == "space-1"
        assert any("SQLite backup is daily" in str(message) for message in seen)
        assert all("SQLite backup is daily" not in str(message) for message in session.store.load())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_tencent_capture_posts_only_user_and_final_assistant(monkeypatch):
    monkeypatch.setenv("TDAI_MEMORY_API_KEY", "test-secret")
    calls = []

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse({"code": 0, "data": {
            "accepted_ids": ["msg-user", "msg-assistant"],
            "accepted_versions": ["v1", "v1"],
            "total_count": 2,
        }})

    monkeypatch.setattr(requests, "post", fake_post)
    provider = TencentMemoryCoreProvider("https://memory.example", service_id="space-1")
    capture = MemoryCapture(
        scope=MemoryScope(user_id="user-1", team_id="team-1", agent_id="agent-1"),
        session_id="session-1",
        user_message="Please explain the test command.",
        assistant_message="Use uv run pytest -q.",
        timeout_seconds=7,
    )

    provider.capture(capture)

    assert len(calls) == 1
    assert calls[0][0] == "https://memory.example/v3/conversation/add"
    assert calls[0][1]["json"] == {
        "team_id": "team-1", "agent_id": "agent-1", "user_id": "user-1",
        "session_id": "session-1",
        "messages": [
            {"role": "user", "content": "Please explain the test command."},
            {"role": "assistant", "content": "Use uv run pytest -q."},
        ],
    }
    assert calls[0][1]["timeout"] == 7
    assert calls[0][1]["headers"]["x-tdai-service-id"] == "space-1"


def test_tencent_capture_timeout_is_uncertain_and_business_error_is_definite(monkeypatch):
    monkeypatch.setenv("TDAI_MEMORY_API_KEY", "test-secret")
    provider = TencentMemoryCoreProvider("https://memory.example", service_id="space-1")
    capture = MemoryCapture(
        scope=_query().scope,
        session_id="session-1",
        user_message="question",
        assistant_message="answer",
    )

    def timeout(_url, **_kwargs):
        raise requests.Timeout("network timeout")

    monkeypatch.setattr(requests, "post", timeout)
    with pytest.raises(MemoryWriteUncertainError):
        provider.capture(capture)

    monkeypatch.setattr(
        requests, "post",
        lambda *_args, **_kwargs: FakeResponse({"code": 403, "message": "denied", "data": None}),
    )
    with pytest.raises(MemoryWriteError):
        provider.capture(capture)
