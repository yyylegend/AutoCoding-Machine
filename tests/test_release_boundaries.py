import pytest

from examples.order_assistant import SAVED_DRAFTS, build_session
from auto_coding_machine import AgentResponse, ToolCall
from auto_coding_machine.engine import CancellationToken
from auto_coding_machine.engine.context_manager import ContextManager


@pytest.mark.parametrize("resume", [False, True])
def test_cancellation_after_last_tool_takes_precedence_over_turn_limit(tmp_path, resume):
    SAVED_DRAFTS.clear()
    events = []
    session = build_session(tmp_path, on_event=lambda event: events.append(event.name))
    session.runtime.loop.budget.max_turns = 2 if resume else 1
    run = session.begin_run("查询订单 A1001 并保存回复草稿")
    target = "save_reply_draft" if resume else "query_order"

    def cancel_after_tool(**event):
        if event["tool_name"] == target:
            run.cancel()

    session.runtime.hooks.on("post_tool", cancel_after_tool)
    result = run.start()
    if resume:
        assert result["status"] == "permission_required"
        result = run.resolve_permission(approved=True)
    assert result["status"] == "cancelled"
    assert events[-1] == "cancelled"
    assert "failed" not in events


def test_cancellation_after_remaining_batch_tool_is_reported(tmp_path):
    events = []
    session = build_session(tmp_path, on_event=lambda event: events.append(event.name))
    loop = session.runtime.loop
    loop.budget.max_turns = 1
    cancel = CancellationToken()
    call = ToolCall("q-1", "query_order", {"order_id": "A1001"})
    messages = [AgentResponse(tool_calls=[call]).to_message()]
    session.runtime.hooks.on("post_tool", lambda **event: cancel.cancel())

    result = loop.run(messages, cancel, resume_batch=[call])

    assert result["status"] == "cancelled"
    assert events[-1] == "cancelled"
    assert messages[-1]["tool_call_id"] == "q-1"


def test_remaining_batch_executes_before_context_compaction(tmp_path):
    session = build_session(tmp_path)
    loop = session.runtime.loop
    loop.budget.max_turns = 1
    loop.context_manager = ContextManager(max_messages=3)
    calls = [ToolCall(f"q-{i}", "query_order", {"order_id": order_id})
             for i, order_id in enumerate(("A1001", "A1002", "A1001", "A1002"))]
    messages = [
        {"role": "user", "content": "查询订单"},
        AgentResponse(tool_calls=calls).to_message(),
        *[session.runtime.tools.execute(call).to_message() for call in calls[:3]],
    ]
    modes = []
    session.runtime.hooks.on(
        "pre_tool", lambda **event: modes.append(loop.context_manager.last_compaction_mode),
    )

    result = loop.run(messages, CancellationToken(), resume_batch=calls[3:])

    assert result["error"] == "max_turns"
    assert modes == ["none"]
    assert [m["tool_call_id"] for m in messages[2:]] == ["q-0", "q-1", "q-2", "q-3"]


def test_receipt_write_failure_is_not_hidden_by_cancellation(tmp_path):
    events = []
    session = build_session(tmp_path, on_event=lambda event: events.append(event.name))
    run = session.begin_run("查询订单 A1001")

    def interrupt_receipt_storage(**event):
        run.cancel()
        # 目录无法作为 JSONL 文件打开，触发真实文件系统写入错误。
        session.store.path = tmp_path

    session.runtime.hooks.on("post_tool", interrupt_receipt_storage)
    result = run.start()

    assert result["status"] == "failed"
    assert result["error"] == "session_write_failed"
    assert result["tool_result"]["tool_call_id"] == "q-1"
    assert events[-1] == "failed"
    assert "cancelled" not in events
