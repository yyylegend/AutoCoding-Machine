import pytest

from examples.order_assistant import SAVED_DRAFTS, build_session
from auto_coding_machine import AgentResponse, ToolCall
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


def _draft(call_id):
    return ToolCall(call_id, "save_reply_draft", {"order_id": "A1001", "content": "草稿"})


def _query(call_id, order_id="A1001"):
    return ToolCall(call_id, "query_order", {"order_id": order_id})


def test_cancellation_after_remaining_batch_tool_is_reported(tmp_path):
    SAVED_DRAFTS.clear()
    events = []
    calls = [_draft("d-1"), _query("q-1")]
    session = build_session(
        tmp_path, model_fn=lambda _messages: AgentResponse(tool_calls=calls),
        on_event=lambda event: events.append(event.name),
    )
    session.runtime.loop.budget.max_turns = 1
    run = session.begin_run("保存草稿并查询订单")

    def cancel_after_query(**event):
        if event["tool_name"] == "query_order":
            run.cancel()

    session.runtime.hooks.on("post_tool", cancel_after_query)
    assert run.start()["status"] == "permission_required"
    result = run.resolve_permission(approved=True)

    assert result["status"] == "cancelled"
    assert events[-1] == "cancelled"
    assert session.store.load()[-1]["tool_call_id"] == "q-1"


def test_remaining_batch_executes_before_context_compaction(tmp_path):
    SAVED_DRAFTS.clear()
    calls = [_query("q-0"), _query("q-1", "A1002"), _draft("d-1"), _query("q-3", "A1002")]
    session = build_session(tmp_path, model_fn=lambda _messages: AgentResponse(tool_calls=calls))
    loop = session.runtime.loop
    loop.budget.max_turns = 1
    loop.context_manager = ContextManager(max_messages=3)
    compactions = []
    session.runtime.hooks.on("compacted", lambda **event: compactions.append(event))
    run = session.begin_run("查询订单")

    assert run.start()["status"] == "permission_required"
    before = len(compactions)
    result = run.resolve_permission(approved=True)

    # 暂停前已有的回执超过消息上限，恢复后剩余调用仍先于压缩完成。
    assert result["error"] == "max_turns"
    assert len(compactions) == before
    receipts = [m["tool_call_id"] for m in session.store.load() if m["role"] == "tool"]
    assert receipts == ["q-0", "q-1", "d-1", "q-3"]


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
