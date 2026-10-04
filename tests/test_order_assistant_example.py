"""订单助手示例的组合验证：公共入口、自定义工具、权限交接与离线启动。"""

import json

from examples.order_assistant import (
    SAVED_DRAFTS,
    build_session,
    run_order_task,
)
from auto_coding_machine.runtime import factory
from auto_coding_machine.runtime import skills as skills_module


def test_order_assistant_runs_business_tools_without_coding_dependencies(tmp_path, monkeypatch):
    SAVED_DRAFTS.clear()
    # 显式预算的调用方不应触发模型窗口查询，演示路径必须能离线启动。
    monkeypatch.setattr(
        factory, "resolve_context_info",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应查询模型窗口")),
    )
    # 不使用技能时也不应扫描技能目录。
    monkeypatch.setattr(
        skills_module, "discover_skills",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("不应扫描技能目录")),
    )

    events = []

    def observe(event):
        events.append((event.name, event.data))

    result = run_order_task(tmp_path, "客户问订单 A1001 到哪了", on_event=observe)

    assert result["status"] == "success"
    assert "未发送" in result["reply"]
    # 业务工具确实执行并留有草稿；示例不发送真实消息。
    assert [d["order_id"] for d in SAVED_DRAFTS] == ["A1001"]
    assert "已发货" in SAVED_DRAFTS[0]["content"]

    names = [name for name, _data in events]
    # 事件顺序：审批前通知，决议先于工具结果，最后正常结束。
    assert "permission_required" in names
    assert names.index("permission_resolved") > names.index("permission_required")
    resolved = events[names.index("permission_resolved")][1]
    assert resolved["decision"] == "approved"
    assert names[-1] == "done"

    session_files = list(
        (tmp_path / ".autocoding/profiles/orders/sessions").glob("*.jsonl")
    )
    assert len(session_files) == 1
    messages = [
        json.loads(line)
        for line in session_files[0].read_text(encoding="utf-8").splitlines()
    ]
    roles = [message["role"] for message in messages]
    assert roles == ["user", "assistant", "tool", "assistant", "tool", "assistant"]
    receipts = [m["tool_call_id"] for m in messages if m["role"] == "tool"]
    assert len(receipts) == 2


def test_order_assistant_tool_schemas_carry_only_business_tools(tmp_path):
    session = build_session(tmp_path)
    runtime = session.runtime
    names = {
        schema["function"]["name"] for schema in runtime.tools.get_schemas()
    }
    assert names == {"query_order", "save_reply_draft"}


def test_order_assistant_pause_exposes_redacted_request(tmp_path):
    SAVED_DRAFTS.clear()
    result = run_order_task(tmp_path, "起草回复", approve=False)

    assert result["status"] == "permission_required"
    request = result["permission_request"]
    assert request["tool_name"] == "save_reply_draft"
    assert request["details"]["order_id"] == "A1001"
    assert SAVED_DRAFTS == []
