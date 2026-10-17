"""AgentRun 的任务生命周期 seam 测试。"""

from auto_coding_machine.engine import (
    AgentResponse,
    BudgetPolicy,
    CancellationToken,
    GuardManager,
    HookManager,
    MachineLoop,
    ToolCall,
    ToolResult,
)
from auto_coding_machine.engine.session_store import SessionStore
from auto_coding_machine.runtime.run import AgentRun
from tests.helpers import ScriptedPermission, scripted_model


class FakeGate:
    def __init__(self):
        self.started = 0

    def start_task(self):
        self.started += 1


class FakeTools:
    def __init__(self):
        self.calls = []

    def execute(self, tool_call):
        self.calls.append(tool_call)
        return ToolResult(tool_call.id, "工具执行成功")


class ReceiptWriteFailingStore:
    """助手消息可以写入，工具回执写入失败。"""

    def __init__(self):
        self.messages = []

    def append(self, message):
        self.messages.append(message)
        if message["role"] == "tool":
            raise OSError("磁盘暂时不可写")


def make_run(model_fn, tools, store, *, hooks=None, gate=None, messages=None, cancel=None):
    loop = MachineLoop(
        model_fn=model_fn,
        tools=tools,
        permission=ScriptedPermission({"write_file", "run_bash"}),
        guard=GuardManager(),
        budget=BudgetPolicy(max_turns=10),
        final_verifier=lambda _messages, response: response.done,
        hooks=hooks or HookManager(),
        session_store=store,
    )
    return AgentRun(loop, store, gate, messages or [{"role": "user", "content": "修改 a.py"}], cancel)


def test_agent_run_approves_tool_and_resumes_with_same_task_state(tmp_path):
    call = ToolCall(id="call-1", name="write_file", arguments={"path": "a.py"})
    model = scripted_model(AgentResponse(tool_calls=[call]), AgentResponse(content="完成", done=True))
    gate = FakeGate()
    tools = FakeTools()
    hooks = HookManager()
    store = SessionStore(tmp_path, "run-1")
    seen = []
    hooks.on("post_tool", lambda **event: seen.append(event))
    cancel = CancellationToken()
    run = make_run(model, tools, store, hooks=hooks, gate=gate, cancel=cancel)

    assert run.start()["status"] == "permission_required"
    result = run.resolve_permission(approved=True)

    assert result == {"status": "success", "reply": "完成"}
    assert gate.started == 1
    assert tools.calls == [call]
    assert run.cancel_token is cancel
    receipt = {"role": "tool", "tool_call_id": "call-1", "content": "工具执行成功"}
    assert model.calls[-1][-1] == receipt
    assert receipt in store.load()
    assert seen[0]["tool_name"] == "write_file"
    assert seen[0]["turn"] == 0


def test_agent_run_stops_with_tool_result_when_session_write_fails():
    call = ToolCall(id="call-1", name="write_file", arguments={"path": "a.py"})
    model = scripted_model(AgentResponse(tool_calls=[call]), AgentResponse(content="继续完成", done=True))
    tools = FakeTools()
    store = ReceiptWriteFailingStore()
    run = make_run(model, tools, store, gate=FakeGate())

    run.start()
    result = run.resolve_permission(approved=True)

    receipt = {"role": "tool", "tool_call_id": "call-1", "content": "工具执行成功"}
    assert result == {"status": "failed", "error": "session_write_failed", "tool_result": receipt}
    assert tools.calls == [call]
    assert len(model.calls) == 1  # 写盘失败后不能继续调用模型或执行更多工具。
    assert run.result is result
    assert store.messages[-1] == receipt
    try:
        run.resolve_permission(approved=True)
    except RuntimeError:
        pass
    else:
        raise AssertionError("同一工具不能重复执行")
    assert tools.calls == [call]


def test_agent_run_reports_denied_tool_to_model_without_executing_it(tmp_path):
    call = ToolCall(id="call-2", name="run_bash", arguments={"command": "del a.py"})
    model = scripted_model(AgentResponse(tool_calls=[call]), AgentResponse(content="请换一种做法"))
    tools = FakeTools()
    run = make_run(model, tools, SessionStore(tmp_path, "run-2"), gate=FakeGate(),
                   messages=[{"role": "user", "content": "删除 a.py"}])

    run.start()
    result = run.resolve_permission(approved=False)

    assert result["status"] == "need_input"
    assert tools.calls == []
    assert model.calls[-1][-1]["role"] == "tool"
    assert model.calls[-1][-1]["content"] == "用户拒绝了这次操作"


def test_agent_run_cancel_marks_its_own_token(tmp_path):
    run = make_run(scripted_model(), FakeTools(), SessionStore(tmp_path, "run-3"), messages=[])

    run.cancel()

    assert run.cancel_token.is_cancelled() is True
