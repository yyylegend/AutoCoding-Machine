"""AgentRun 的任务生命周期 seam 测试。"""

from src.engine import CancellationToken, HookManager, ToolCall, ToolResult
from src.engine.session_store import SessionStore
from src.runtime.run import AgentRun


class FakeLoop:
    def __init__(self, results):
        self.results = list(results)
        self.started_messages = []
        self.calls = []

    def start_task(self, messages):
        self.started_messages.append(messages)

    def run(self, messages, cancel):
        self.calls.append((messages, cancel))
        return self.results.pop(0)


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


def test_agent_run_approves_tool_and_resumes_with_same_task_state(tmp_path):
    messages = [{"role": "user", "content": "修改 a.py"}]
    call = ToolCall(id="call-1", name="write_file", arguments={"path": "a.py"})
    pending = {
        "status": "permission_required",
        "pending_tool_call": call,
        "messages": messages,
        "turn": 2,
    }
    loop = FakeLoop([pending, {"status": "success", "reply": "完成"}])
    gate = FakeGate()
    tools = FakeTools()
    hooks = HookManager()
    store = SessionStore(tmp_path, "run-1")
    seen = []
    hooks.on("post_tool", lambda **event: seen.append(event))
    cancel = CancellationToken()
    run = AgentRun(loop, tools, hooks, store, gate, messages, cancel)

    assert run.start()["status"] == "permission_required"
    result = run.resolve_permission(approved=True)

    assert result == {"status": "success", "reply": "完成"}
    assert gate.started == 1
    assert loop.started_messages == [messages]
    assert tools.calls == [call]
    assert loop.calls[0][1] is cancel
    assert loop.calls[1][1] is cancel
    assert messages[-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": "工具执行成功",
    }
    assert store.load() == [messages[-1]]
    assert seen[0]["tool_name"] == "write_file"
    assert seen[0]["turn"] == 2


def test_agent_run_reports_denied_tool_to_model_without_executing_it(tmp_path):
    messages = [{"role": "user", "content": "删除 a.py"}]
    call = ToolCall(id="call-2", name="run_bash", arguments={"command": "del a.py"})
    pending = {
        "status": "permission_required",
        "pending_tool_call": call,
        "messages": messages,
        "turn": 0,
    }
    loop = FakeLoop([pending, {"status": "need_input", "reply": "请换一种做法"}])
    tools = FakeTools()
    run = AgentRun(
        loop,
        tools,
        HookManager(),
        SessionStore(tmp_path, "run-2"),
        FakeGate(),
        messages,
        CancellationToken(),
    )

    run.start()
    result = run.resolve_permission(approved=False)

    assert result["status"] == "need_input"
    assert tools.calls == []
    resumed_messages = loop.calls[-1][0]
    assert resumed_messages[-1]["role"] == "tool"
    assert resumed_messages[-1]["content"] == "用户拒绝了这次操作"


def test_agent_run_cancel_marks_its_own_token(tmp_path):
    run = AgentRun(
        FakeLoop([]),
        FakeTools(),
        HookManager(),
        SessionStore(tmp_path, "run-3"),
        FakeGate(),
        [],
        None,
    )

    run.cancel()

    assert run.cancel_token.is_cancelled() is True
