"""一次任务的生命周期契约：批次工具调用、审批期间取消与轮数预算。

用确定性模型和本地工具替身复现三种可疑路径，不依赖真实 LLM 或外部服务。
"""

from auto_coding_machine.engine import (
    AgentResponse,
    BudgetPolicy,
    GuardManager,
    HookManager,
    MachineLoop,
    PermissionDecision,
    ToolCall,
    ToolResult,
)
from auto_coding_machine.runtime.run import AgentRun


class RecordingTools:
    """记录执行过的工具调用，返回固定回执。"""

    def __init__(self):
        self.executed = []

    def execute(self, tool_call):
        self.executed.append(tool_call.id)
        return ToolResult(tool_call.id, f"{tool_call.id} 的结果")


class ScriptedPermission:
    """按工具名返回固定权限决定，模拟写操作需要确认。"""

    def __init__(self, ask_names=()):
        self.ask_names = set(ask_names)

    def check(self, tool_call):
        if tool_call.name in self.ask_names:
            return PermissionDecision.ASK
        return PermissionDecision.AUTO


def scripted_model(*responses):
    """按顺序返回脚本化回复，并记录每次模型调用收到的完整历史。"""
    remaining = list(responses)

    def model_fn(messages):
        model_fn.calls.append(list(messages))
        return remaining.pop(0)

    model_fn.calls = []
    return model_fn


def make_run(model_fn, tools, permission, *, max_turns=10, hooks=None, messages=None):
    hooks = hooks if hooks is not None else HookManager()
    loop = MachineLoop(
        model_fn=model_fn,
        tools=tools,
        permission=permission,
        guard=GuardManager(),
        budget=BudgetPolicy(max_turns=max_turns),
        final_verifier=lambda msgs, resp: resp.done,
        hooks=hooks,
    )
    if messages is None:
        messages = [{"role": "user", "content": "处理任务"}]
    return AgentRun(
        loop,
        tools,
        hooks,
        None,
        None,
        messages,
    )


def tool_receipts(model_fn, call_index=1):
    """取第 call_index 次模型调用看到的工具回执（0-based）。"""
    return [
        message
        for message in model_fn.calls[call_index]
        if message.get("role") == "tool"
    ]


def test_batch_resumes_remaining_calls_in_order_without_reexecuting(tmp_path):
    """一次回复里的多个调用：审批前后顺序不变，剩余调用不被遗漏，已执行的不重复。"""
    tools = RecordingTools()
    model = scripted_model(
        AgentResponse(
            content="先查再改再收尾",
            tool_calls=[
                ToolCall(id="t1", name="lookup", arguments={}),
                ToolCall(id="t2", name="confirm_write", arguments={}),
                ToolCall(id="t3", name="finish", arguments={}),
            ],
        ),
        AgentResponse(content="完成", done=True),
    )
    run = make_run(model, tools, ScriptedPermission({"confirm_write"}))

    result = run.start()

    # 暂停时只执行了 t1；批次未完成前不调用模型。
    assert result["status"] == "permission_required"
    assert result["permission_request"]["tool_call_id"] == "t2"
    assert tools.executed == ["t1"]
    assert len(model.calls) == 1

    result = run.resolve_permission(approved=True)

    assert result["status"] == "success"
    # t2、t3 按原顺序执行；t1 不因恢复而重复执行。
    assert tools.executed == ["t1", "t2", "t3"]
    # 下一次模型调用前，三个调用都有匹配回执。
    assert [m["tool_call_id"] for m in tool_receipts(model)] == ["t1", "t2", "t3"]


def test_denied_tool_keeps_explicit_rule_for_rest_of_batch():
    """拒绝单个调用后，同批剩余调用继续按各自的权限检查处理。"""
    tools = RecordingTools()
    model = scripted_model(
        AgentResponse(
            tool_calls=[
                ToolCall(id="t1", name="lookup", arguments={}),
                ToolCall(id="t2", name="confirm_write", arguments={}),
                ToolCall(id="t3", name="finish", arguments={}),
            ],
        ),
        AgentResponse(content="完成", done=True),
    )
    run = make_run(model, tools, ScriptedPermission({"confirm_write"}))

    assert run.start()["status"] == "permission_required"
    result = run.resolve_permission(approved=False)

    assert result["status"] == "success"
    # t2 未执行，t3 仍按自己的检查执行。
    assert tools.executed == ["t1", "t3"]
    receipts = tool_receipts(model)
    assert [m["tool_call_id"] for m in receipts] == ["t1", "t2", "t3"]
    assert "拒绝" in receipts[1]["content"]


def test_approval_arriving_after_cancel_does_not_execute_tool():
    """等待审批期间取消后，到达的批准不能执行工具，结果与事件一致。"""
    tools = RecordingTools()
    hooks = HookManager()
    events = []
    hooks.on_event(lambda event: events.append(event.name))
    model = scripted_model(
        AgentResponse(tool_calls=[ToolCall(id="t2", name="confirm_write", arguments={})]),
        AgentResponse(content="完成", done=True),
    )
    run = make_run(model, tools, ScriptedPermission({"confirm_write"}), hooks=hooks)

    assert run.start()["status"] == "permission_required"
    run.cancel()
    result = run.resolve_permission(approved=True)

    assert result["status"] == "cancelled"
    assert tools.executed == []
    # 事件顺序一致：决定已上报，工具标记未执行，任务以取消结束。
    assert events[-3:] == ["permission_resolved", "post_tool", "cancelled"]
    receipts = [m for m in run._messages if m.get("role") == "tool"]
    assert receipts[-1]["tool_call_id"] == "t2"
    assert "未执行" in receipts[-1]["content"]


def test_cancel_after_pause_accounts_for_rest_of_batch():
    """取消后批次内剩余调用也有明确的未执行回执。"""
    tools = RecordingTools()
    model = scripted_model(
        AgentResponse(
            tool_calls=[
                ToolCall(id="t2", name="confirm_write", arguments={}),
                ToolCall(id="t3", name="finish", arguments={}),
            ],
        ),
        AgentResponse(content="完成", done=True),
    )
    run = make_run(model, tools, ScriptedPermission({"confirm_write"}))

    assert run.start()["status"] == "permission_required"
    run.cancel()
    result = run.resolve_permission(approved=True)

    assert result["status"] == "cancelled"
    assert tools.executed == []
    receipts = [m for m in run._messages if m.get("role") == "tool"]
    assert [m["tool_call_id"] for m in receipts] == ["t2", "t3"]
    assert all("未执行" in m["content"] for m in receipts)


def test_consumed_permission_decision_cannot_run_tool_twice():
    """已消费的权限决定重复提交会报错，且不会重复执行工具。"""
    tools = RecordingTools()
    model = scripted_model(
        AgentResponse(tool_calls=[ToolCall(id="t2", name="confirm_write", arguments={})]),
        AgentResponse(content="完成", done=True),
    )
    run = make_run(model, tools, ScriptedPermission({"confirm_write"}))

    assert run.start()["status"] == "permission_required"
    run.cancel()
    assert run.resolve_permission(approved=True)["status"] == "cancelled"

    try:
        run.resolve_permission(approved=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError("已消费的权限决定不能重复提交")
    assert tools.executed == []


def test_permission_pauses_do_not_reset_turn_budget():
    """暂停与批准不获得新的轮数预算；事件轮次与实际计数一致。"""
    tools = RecordingTools()
    hooks = HookManager()
    events = []
    hooks.on_event(lambda event: events.append(event))
    model = scripted_model(
        AgentResponse(tool_calls=[ToolCall(id="t1", name="confirm_write", arguments={})]),
        AgentResponse(tool_calls=[ToolCall(id="t2", name="confirm_write", arguments={})]),
        # 预算耗尽前不应被调用；只有预算被重置才会消耗这条回复。
        AgentResponse(content="第三回合", done=True),
    )
    run = make_run(
        model, tools, ScriptedPermission({"confirm_write"}),
        hooks=hooks, max_turns=2,
    )

    first = run.start()
    assert first["status"] == "permission_required"
    assert first["permission_request"]["turn"] == 0

    second = run.resolve_permission(approved=True)
    assert second["status"] == "permission_required"
    assert second["permission_request"]["turn"] == 1

    result = run.resolve_permission(approved=True)

    # 每次暂停恢复都接着上一次的轮次；两次批准没有换到新预算。
    assert result["status"] == "failed"
    assert result["error"] == "max_turns"
    assert len(model.calls) == 2
    post_tool_turns = [
        event.data.get("turn") for event in events if event.name == "post_tool"
    ]
    assert post_tool_turns == [0, 1]
