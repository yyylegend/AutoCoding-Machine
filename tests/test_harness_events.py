import pytest

from auto_coding_machine import (
    AgentEvent,
    AgentResponse,
    Profile,
    ToolCall,
    open_harness_session,
)


def test_public_event_observer_is_scoped_to_session_and_cannot_control_run(tmp_path):
    events = []

    def observe(event):
        events.append(event)
        return False

    session = open_harness_session(
        tmp_path,
        lambda _messages: AgentResponse(content="完成", done=True),
        profile=Profile(kind="review", name="review", tools=()),
        on_event=observe,
    )

    first = session.begin_run("检查第一个文件").start()
    second = session.begin_run("检查第二个文件").start()

    assert first["status"] == second["status"] == "success"
    assert [event.name for event in events if event.name == "done"] == ["done", "done"]
    assert all(isinstance(event, AgentEvent) for event in events)


def test_permission_pause_exposes_only_redacted_request_view(tmp_path):
    secret = "ghp_testcredential123456"
    events = []
    call = ToolCall(
        id="call-write",
        name="write_file",
        arguments={
            "path": "safe.py",
            "content": f"Authorization: Bearer {secret}",
            "api_token": "short-secret",
            "secret_value": "another-secret",
            "note": secret,
            "extra": "x" * 201,
        },
    )
    session = open_harness_session(
        tmp_path,
        lambda _messages: AgentResponse(tool_calls=[call]),
        on_event=events.append,
    )

    result = session.begin_run("写入文件").start()

    assert result["status"] == "permission_required"
    assert "pending_tool_call" not in result
    assert "messages" not in result
    request = result["permission_request"]
    assert request["tool_name"] == "write_file"
    assert "safe.py" in request["summary"]
    assert request["details"]["path"] == "safe.py"
    assert request["details"]["api_token"] == "[REDACTED]"
    assert request["details"]["secret_value"] == "[REDACTED]"
    assert request["details"]["note"] == "[REDACTED]"
    assert "[REDACTED]" in request["details"]["content"]
    assert secret not in request["details"]["content"]
    assert request["details"]["extra"] == "[HIDDEN: value exceeds 200 characters]"
    assert next(event for event in events if event.name == "permission_required").data == {
        "request": request,
    }
    assert secret not in repr(events)
    assert secret not in repr(result)


@pytest.mark.parametrize(
    ("approved", "path", "created", "tool_error"),
    [
        (True, "created.py", True, False),
        (False, "denied.py", False, True),
        (True, "../outside.py", False, True),
    ],
)
def test_permission_resolution_event_precedes_tool_outcome(
    tmp_path, approved, path, created, tool_error
):
    events = []
    responses = iter([
        AgentResponse(tool_calls=[ToolCall(
            id="call-write",
            name="write_file",
            arguments={"path": path, "content": "hello"},
        )]),
        AgentResponse(content="处理完成", done=True),
    ])
    session = open_harness_session(
        tmp_path,
        lambda _messages: next(responses),
        on_event=events.append,
    )
    run = session.begin_run("写入文件")

    assert run.start()["status"] == "permission_required"
    result = run.resolve_permission(approved=approved)

    assert result["status"] == "success"
    resolution = next(event for event in events if event.name == "permission_resolved")
    tool_result = next(event for event in events if event.name == "post_tool")
    assert resolution.data["decision"] == ("approved" if approved else "denied")
    assert resolution.data["tool_call_id"] == "call-write"
    assert tool_result.data["error"] is tool_error
    assert "result_content" not in tool_result.data
    assert events.index(resolution) < events.index(tool_result)
    assert (tmp_path / path).is_file() is created


def test_observer_exception_isolated_and_not_written_to_logs(tmp_path, caplog):
    sentinel = "observer-secret-value"

    def broken(_event):
        raise RuntimeError(sentinel)

    session = open_harness_session(
        tmp_path,
        lambda _messages: AgentResponse(content="完成", done=True),
        profile=Profile(kind="review", name="review", tools=()),
        on_event=broken,
    )

    result = session.begin_run("检查代码").start()

    assert result["status"] == "success"
    assert "公共事件观察回调异常" in caplog.text
    assert "RuntimeError" in caplog.text
    assert sentinel not in caplog.text


def test_mutating_public_event_does_not_change_tool_execution(tmp_path):
    events = []

    def observe(event):
        events.append(event)
        if event.name == "permission_required":
            event.data["request"]["details"]["path"] = "changed.py"

    responses = iter([
        AgentResponse(tool_calls=[ToolCall(
            id="call-write",
            name="write_file",
            arguments={"path": "original.py", "content": "hello"},
        )]),
        AgentResponse(content="完成", done=True),
    ])
    session = open_harness_session(
        tmp_path,
        lambda _messages: next(responses),
        on_event=observe,
    )
    run = session.begin_run("写文件")

    paused = run.start()
    request = paused["permission_request"]
    request["turn"] = 99
    result = run.resolve_permission(approved=True)

    assert request["details"]["path"] == "original.py"
    assert result["status"] == "success"
    assert (tmp_path / "original.py").read_text(encoding="utf-8") == "hello"
    assert not (tmp_path / "changed.py").exists()
    resolution = next(event for event in events if event.name == "permission_resolved")
    assert resolution.data["turn"] == 0
