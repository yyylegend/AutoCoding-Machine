import json

from auto_coding_machine import AgentResponse, ModelAdapter, ToolCall
from auto_coding_machine.runtime import factory
from examples.harness_review import review_workspace


def test_read_only_review_example_uses_harness_and_persists_session(tmp_path, monkeypatch):
    from pathlib import Path

    sample = tmp_path / "sample.py"
    sample.write_text("def divide(a, b):\n    return a / b\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(
        factory, "resolve_context_info",
        lambda _model: {"window": None, "source": "test"},
    )
    monkeypatch.setattr(
        factory, "profile_token_budget", lambda _profile, **_kwargs: 8000
    )

    calls = []

    def fixed_model(_adapter, messages):
        calls.append(messages)
        tool_names = {
            schema["function"]["name"]
            for schema in _adapter.tools_schemas
        }
        assert "read_file" in tool_names
        assert not tool_names & {"write_file", "edit_file", "run_test", "run_bash"}
        if len(calls) == 1:
            return AgentResponse(tool_calls=[ToolCall(
                "read-1", "read_file", {"path": "sample.py"}
            )])
        assert any(
            message.get("role") == "tool" and "return a / b" in message.get("content", "")
            for message in messages
        )
        return AgentResponse(content="Review result", done=True)

    monkeypatch.setattr(ModelAdapter, "call", fixed_model)

    result = review_workspace(tmp_path, "检查 sample.py 的除零风险")

    assert result == {"status": "success", "reply": "Review result"}
    session_files = list(
        (tmp_path / ".autocoding/profiles/review/sessions").glob("*.jsonl")
    )
    assert len(session_files) == 1
    messages = [json.loads(line) for line in session_files[0].read_text(encoding="utf-8").splitlines()]
    assert [message["role"] for message in messages] == [
        "user", "assistant", "tool", "assistant",
    ]
    assert messages[0]["content"] == "检查 sample.py 的除零风险"
    assert messages[-1]["content"] == "Review result"
    assert len(calls) == 2
