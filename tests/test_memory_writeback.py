"""显式外部记忆写回的公共 Harness 行为。"""

from pathlib import Path

from auto_coding_machine.engine import AgentResponse, ToolCall
from auto_coding_machine.memory import (
    MemoryCapture,
    MemoryScope,
    MemoryWriteError,
    MemoryWriteUncertainError,
)
from auto_coding_machine.profiles.config import load_profile
from auto_coding_machine.runtime import open_harness_session


class RecordingMemoryWriter:
    def __init__(self):
        self.captures = []

    def capture(self, item):
        self.captures.append(item)


class UncertainMemoryWriter:
    def __init__(self):
        self.calls = 0

    def capture(self, _item):
        self.calls += 1
        raise MemoryWriteUncertainError("response timed out after send")


class FailedMemoryWriter:
    def capture(self, _item):
        raise MemoryWriteError("gateway rejected capture")


def _open_session(tmp_path, model_fn, writer, monkeypatch):
    from auto_coding_machine.runtime import factory

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(factory, "resolve_context_info", lambda _model: {"window": None, "source": "test"})
    monkeypatch.setattr(factory, "profile_token_budget", lambda _profile, **_kwargs: 8000)
    options = {"memory_writer": writer} if writer is not None else {}
    if writer is not None:
        options["memory_scope"] = MemoryScope(
            user_id="user-1", team_id="team-1", agent_id="reviewer-1",
        )
    return open_harness_session(tmp_path, model_fn, profile=load_profile("review"), **options)


def test_explicit_writer_captures_only_successful_final_turn(tmp_path, monkeypatch):
    writer = RecordingMemoryWriter()
    session = _open_session(
        tmp_path,
        lambda _messages: AgentResponse(content="Use uv run pytest -q.", done=True),
        writer, monkeypatch,
    )

    result = session.begin_run("How do I run this project's tests?").start()

    assert result["status"] == "success"
    assert result["memory_writeback"] == {"status": "saved"}
    assert writer.captures == [MemoryCapture(
        scope=MemoryScope(user_id="user-1", team_id="team-1", agent_id="reviewer-1"),
        session_id=session.store.session_id,
        user_message="How do I run this project's tests?",
        assistant_message="Use uv run pytest -q.",
    )]


def test_writeback_is_off_without_explicit_writer(tmp_path, monkeypatch):
    session = _open_session(
        tmp_path,
        lambda _messages: AgentResponse(content="Done.", done=True),
        None, monkeypatch,
    )

    result = session.begin_run("Do a small task.").start()

    assert result["status"] == "success"
    assert "memory_writeback" not in result


def test_permission_resumed_run_captures_only_user_and_final_reply(tmp_path, monkeypatch):
    from auto_coding_machine.runtime import factory

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(factory, "resolve_context_info", lambda _model: {"window": None, "source": "test"})
    monkeypatch.setattr(factory, "profile_token_budget", lambda _profile, **_kwargs: 8000)
    writer = RecordingMemoryWriter()
    responses = iter([
        AgentResponse(tool_calls=[ToolCall(
            "call-write", "write_file", {"path": "created.txt", "content": "content"},
        )]),
        AgentResponse(content="Created the file.", done=True),
    ])
    session = open_harness_session(
        tmp_path,
        lambda _messages: next(responses),
        profile=load_profile("coding"),
        memory_writer=writer,
        memory_scope=MemoryScope(user_id="user-1", team_id="team-1", agent_id="coder-1"),
    )
    run = session.begin_run("Create a small file.")

    assert run.start()["status"] == "permission_required"
    assert writer.captures == []
    result = run.resolve_permission(True)

    assert result["status"] == "success"
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "content"
    assert len(writer.captures) == 1
    assert writer.captures[0].user_message == "Create a small file."
    assert writer.captures[0].assistant_message == "Created the file."


def test_writer_skips_turn_that_needs_more_user_input(tmp_path, monkeypatch):
    writer = RecordingMemoryWriter()
    session = _open_session(
        tmp_path,
        lambda _messages: AgentResponse(content="Which test file should I inspect?", done=False),
        writer, monkeypatch,
    )

    result = session.begin_run("Check the tests.").start()

    assert result["status"] == "need_input"
    assert writer.captures == []


def test_writer_skips_cancelled_turn(tmp_path, monkeypatch):
    writer = RecordingMemoryWriter()
    session = _open_session(
        tmp_path,
        lambda _messages: (_ for _ in ()).throw(AssertionError("cancelled run must not call model")),
        writer, monkeypatch,
    )
    run = session.begin_run("Please stop.")
    run.cancel()

    assert run.start()["status"] == "cancelled"
    assert writer.captures == []


def test_write_timeout_is_visible_and_not_retried(tmp_path, monkeypatch):
    writer = UncertainMemoryWriter()
    session = _open_session(
        tmp_path,
        lambda _messages: AgentResponse(content="Done.", done=True),
        writer, monkeypatch,
    )

    result = session.begin_run("Finish this small task.").start()

    assert result["status"] == "success"
    assert result["memory_writeback"] == {
        "status": "unknown",
        "error_type": "MemoryWriteUncertainError",
    }
    assert writer.calls == 1
    assert [message["role"] for message in session.store.load()] == ["user", "assistant"]


def test_definite_write_failure_is_reported_without_failing_the_agent_task(tmp_path, monkeypatch):
    session = _open_session(
        tmp_path,
        lambda _messages: AgentResponse(content="Done.", done=True),
        FailedMemoryWriter(), monkeypatch,
    )

    result = session.begin_run("Do a small task.").start()

    assert result["status"] == "success"
    assert result["memory_writeback"] == {
        "status": "failed",
        "error_type": "MemoryWriteError",
    }
