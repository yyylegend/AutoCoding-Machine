"""Agent 状态栏的运行时投影测试。"""

from src.engine import (
    AgentResponse,
    BudgetPolicy,
    CancellationToken,
    MachineLoop,
    PermissionManager,
    ToolCall,
    ToolResult,
)
from src.engine.guard_manager import GuardManager
from src.engine.session_store import SessionStore, sessions_dir_for
from src.profiles.config import Profile
from src.runtime.factory import create_runtime
from src.runtime.run import AgentRun
from src.runtime.status import AgentStatusBar


def test_status_is_ephemeral_and_updates_after_tool_call(tmp_path):
    calls = []
    responses = iter([
        AgentResponse(
            tool_calls=[
                ToolCall(id="call-1", name="read_file", arguments={"path": "a.py"})
            ]
        ),
        AgentResponse(content="完成", done=True),
    ])

    class Tools:
        def execute(self, tool_call):
            return ToolResult(tool_call.id, "文件内容")

    def model_fn(messages):
        calls.append(list(messages))
        return next(responses)

    store = SessionStore(sessions_dir_for(tmp_path), "status-session")
    messages = [
        {"role": "system", "content": "稳定规则"},
        {"role": "user", "content": "读取 a.py 并解释"},
    ]
    loop = MachineLoop(
        model_fn=model_fn,
        tools=Tools(),
        permission=PermissionManager(),
        guard=GuardManager(),
        budget=BudgetPolicy(max_turns=3),
        final_verifier=lambda _messages, response: response.done,
        session_store=store,
        status_bar=AgentStatusBar(tmp_path, profile="coding", model="test-model"),
    )

    result = loop.run(messages, CancellationToken())

    assert result["status"] == "success"
    first_status = calls[0][-1]
    second_status = calls[1][-1]
    assert first_status["role"] == "user"
    assert "<agent_status" in first_status["content"]
    assert "goal: 读取 a.py 并解释" in first_status["content"]
    assert "turn: 1/3" in first_status["content"]
    assert "tool_calls: 0" in first_status["content"]
    assert "turn: 2/3" in second_status["content"]
    assert "tool_calls: 1" in second_status["content"]
    assert "last_tool: read_file" in second_status["content"]
    assert all("<agent_status" not in str(message) for message in messages)
    assert all("<agent_status" not in str(message) for message in store.load())


def test_factory_enables_status_bar_by_default(tmp_path):
    class Tools:
        def get_manager(self):
            return object()

        def get_schemas(self):
            return []

    profile = Profile(name="status-review", kind="review", tools=())
    runtime = create_runtime(
        workspace=tmp_path,
        model_fn=lambda _messages: AgentResponse(content="完成", done=True),
        tools=Tools(),
        profile=profile,
        context_manager=object(),
        context_selector=object(),
        permission=object(),
        guard=object(),
        base_injections=[],
    )

    assert isinstance(runtime.status_bar, AgentStatusBar)
    assert runtime.loop.status_bar is runtime.status_bar


def test_status_records_denied_permission_resolved_by_agent_run(tmp_path):
    calls = []

    class Tools:
        def execute(self, _tool_call):
            raise AssertionError("被拒绝的工具不能执行")

    def model_fn(messages):
        calls.append(list(messages))
        if len(calls) == 1:
            return AgentResponse(tool_calls=[
                ToolCall(id="call-2", name="run_bash", arguments={"command": "del a.py"})
            ])
        return AgentResponse(content="已停止删除", done=True)

    loop = MachineLoop(
        model_fn=model_fn,
        tools=Tools(),
        permission=PermissionManager(),
        guard=GuardManager(),
        budget=BudgetPolicy(max_turns=3),
        final_verifier=lambda _messages, response: response.done,
        status_bar=AgentStatusBar(tmp_path, profile="coding", model="test-model"),
    )
    run = AgentRun(
        loop,
        Tools(),
        loop.hooks,
        SessionStore(sessions_dir_for(tmp_path), "status-denied"),
        None,
        [{"role": "user", "content": "删除 a.py"}],
    )

    assert run.start()["status"] == "permission_required"
    assert run.resolve_permission(approved=False)["status"] == "success"
    assert "last_failure: run_bash: permission" in calls[-1][-1]["content"]
