"""Runtime 组件组装 seam 测试。"""

from auto_coding_machine.engine import BudgetPolicy, HookManager
from auto_coding_machine.profiles.config import Profile
from auto_coding_machine.runtime import open_harness_session
from auto_coding_machine.runtime.factory import (
    RuntimeComponents,
    build_runtime_components,
)


class FakeTools:
    def get_manager(self):
        return object()

    def get_schemas(self):
        return []


class FakeGate:
    def before_tool(self, **_):
        pass

    def after_tool(self, **_):
        pass


def test_runtime_component_builder_preserves_injected_adapters(tmp_path):
    profile = Profile(name="assembly-review", kind="review", tools=())
    tools = FakeTools()
    hooks = HookManager()
    context_manager = object()
    context_selector = object()
    completion_gate = FakeGate()
    permission = object()
    guard = object()
    budget = BudgetPolicy(max_turns=7)
    status_bar = object()

    components = build_runtime_components(
        workspace=tmp_path,
        profile=profile,
        tools=tools,
        hooks=hooks,
        context_manager=context_manager,
        context_selector=context_selector,
        completion_gate=completion_gate,
        permission=permission,
        guard=guard,
        budget=budget,
        status_bar=status_bar,
    )

    assert components.tools is tools
    assert components.hooks is hooks
    assert components.context_manager is context_manager
    assert components.context_selector is context_selector
    assert components.completion_gate is completion_gate
    assert components.permission is permission
    assert components.guard is guard
    assert components.budget is budget
    assert components.status_bar is status_bar


def test_runtime_components_are_a_single_shared_set():
    fields = dict(
        tools=object(),
        permission=object(),
        guard=object(),
        context_manager=object(),
        context_selector=object(),
        request_view=object(),
        completion_gate=object(),
        status_bar=object(),
        hooks=object(),
        budget=object(),
        context_info={"window": None, "source": "provided"},
        token_budget=None,
    )

    components = RuntimeComponents(**fields)

    assert components.tools is fields["tools"]
    assert components.context_info == {"window": None, "source": "provided"}


def test_public_entry_opens_and_resumes_session_without_component_assembly(tmp_path, monkeypatch):
    from pathlib import Path

    from auto_coding_machine.engine import AgentResponse
    from auto_coding_machine.profiles.config import load_profile
    from auto_coding_machine.runtime import factory

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(factory, "resolve_context_info", lambda _model: {"window": None, "source": "test"})
    monkeypatch.setattr(factory, "profile_token_budget", lambda _profile, **_kwargs: 8000)
    profile = load_profile("review")
    model_fn = lambda _messages: AgentResponse(content="已完成", done=True)

    session = open_harness_session(tmp_path, model_fn, profile=profile)
    result = session.begin_run("检查代码").start()
    session.refresh()
    resumed = open_harness_session(
        tmp_path, model_fn, profile=profile, resume=session.store.session_id
    )

    assert result == {"status": "success", "reply": "已完成"}
    assert [message["role"] for message in resumed.history] == ["user", "assistant"]
    assert resumed.history[-1]["content"] == "已完成"


def test_public_entry_resolves_permission_with_one_run(tmp_path, monkeypatch):
    from pathlib import Path

    from auto_coding_machine.engine import AgentResponse, ToolCall
    from auto_coding_machine.profiles.config import load_profile
    from auto_coding_machine.runtime import factory

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(factory, "resolve_context_info", lambda _model: {"window": None, "source": "test"})
    monkeypatch.setattr(factory, "profile_token_budget", lambda _profile, **_kwargs: 8000)
    responses = iter([
        AgentResponse(tool_calls=[ToolCall(
            "call-1", "write_file", {"path": "a.py", "content": "hello"}
        )]),
        AgentResponse(content="完成", done=True),
    ])
    session = open_harness_session(
        tmp_path, lambda _messages: next(responses), profile=load_profile("coding")
    )

    run = session.begin_run("创建 a.py")
    assert run.start()["status"] == "permission_required"
    assert run.resolve_permission(True)["status"] == "success"
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "hello"
    assert [message["role"] for message in session.store.load()] == [
        "user", "assistant", "tool", "assistant",
    ]


def test_public_entry_cancels_without_calling_model(tmp_path, monkeypatch):
    from pathlib import Path

    from auto_coding_machine.profiles.config import load_profile
    from auto_coding_machine.runtime import factory

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(factory, "resolve_context_info", lambda _model: {"window": None, "source": "test"})
    monkeypatch.setattr(factory, "profile_token_budget", lambda _profile, **_kwargs: 8000)

    def model_fn(_messages):
        raise AssertionError("取消后不能调用模型")

    session = open_harness_session(tmp_path, model_fn, profile=load_profile("review"))
    run = session.begin_run("先别执行")
    run.cancel()

    assert run.start() == {"status": "cancelled"}
    assert session.store.load()[-1] == {"role": "user", "content": "先别执行"}


def test_public_entry_accepts_store_without_file_path_when_trace_enabled(tmp_path, monkeypatch):
    from dataclasses import replace
    from pathlib import Path

    from auto_coding_machine.engine import AgentResponse
    from auto_coding_machine.engine.session_store import SessionStore
    from auto_coding_machine.profiles.config import load_profile
    from auto_coding_machine.runtime import factory

    class InMemoryStore:
        session_id = "ephemeral-session"

        def __init__(self):
            self.messages = []

        def append(self, message):
            self.messages.append(message)

        def load(self):
            return list(self.messages)

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(factory, "resolve_context_info", lambda _model: {"window": None, "source": "test"})
    monkeypatch.setattr(factory, "profile_token_budget", lambda _profile, **_kwargs: 8000)
    profile = replace(load_profile("review"), trace_enabled=True)
    store = InMemoryStore()
    old = SessionStore(profile.state_dir(tmp_path) / "sessions", "old-session")
    old.append({"role": "user", "content": "继续处理上下文压缩超时问题"})
    old.append({"role": "assistant", "content": "旧会话秘密样本"})
    seen = []

    def model_fn(messages):
        seen.extend(messages)
        return AgentResponse(content="已完成", done=True)

    session = open_harness_session(
        tmp_path, model_fn,
        profile=profile, session_store=store,
    )
    result = session.begin_run("继续看看上下文压缩的超时兜底").start()

    assert result == {"status": "success", "reply": "已完成"}
    assert [message["role"] for message in store.load()] == ["user", "assistant"]
    assert all("旧会话秘密样本" not in str(message.get("content")) for message in seen)
    trace_path = profile.state_dir(tmp_path) / "runs" / "ephemeral-session.jsonl"
    assert trace_path.is_file()


def test_public_entry_composes_tools_memory_and_session(tmp_path, monkeypatch):
    from pathlib import Path

    from auto_coding_machine.engine import AgentResponse, ToolCall, ToolResult
    from auto_coding_machine.engine.tool_manager import tool
    from auto_coding_machine.memory import InMemoryMemoryProvider, MemoryHit, MemoryScope
    from auto_coding_machine.runtime import factory
    from auto_coding_machine.runtime.tools import ProfileTools

    executed = []
    captures = []
    seen = []

    class Writer:
        def capture(self, turn):
            captures.append(turn)

    class EchoTool:
        @staticmethod
        def schema():
            return {"type": "function", "function": {
                "name": "echo", "description": "Repeat text",
                "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
            }}

        @staticmethod
        @tool("echo", permission="ask")
        def execute(tool_call, _sandbox, _max_output_chars):
            executed.append(tool_call.arguments["text"])
            return ToolResult(tool_call.id, tool_call.arguments["text"])

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(factory, "resolve_context_info", lambda _model: {"window": None, "source": "test"})
    monkeypatch.setattr(factory, "profile_token_budget", lambda _profile, **_kwargs: 8000)
    profile = Profile(name="custom-tools", kind="review", tools=(), prompt="Review")
    tools = ProfileTools(tmp_path, profile)
    tools.register(EchoTool)
    scope = MemoryScope(user_id="user-1", team_id="team-1", agent_id="agent-1")
    provider = InMemoryMemoryProvider([
        MemoryHit("say hello: prefer a short reply", "note-1", scope),
    ])
    responses = iter([
        AgentResponse(tool_calls=[ToolCall("call-1", "echo", {"text": "hello"})]),
        AgentResponse(content="done", done=True),
    ])

    def model_fn(messages):
        seen.append(list(messages))
        return next(responses)

    session = open_harness_session(
        tmp_path, model_fn, profile=profile, tools=tools,
        memory_provider=provider, memory_writer=Writer(), memory_scope=scope,
    )
    run = session.begin_run("say hello")

    assert [item["function"]["name"] for item in tools.get_schemas()] == ["echo"]
    assert run.start()["status"] == "permission_required"
    assert executed == []
    assert captures == []
    assert any("prefer a short reply" in str(message) for message in seen[0])
    result = run.resolve_permission(True)
    assert result["status"] == "success"
    assert result["memory_writeback"] == {"status": "saved"}
    assert executed == ["hello"]
    assert [message["role"] for message in session.store.load()] == [
        "user", "assistant", "tool", "assistant",
    ]
    assert all("prefer a short reply" not in str(message) for message in session.store.load())
    assert len(captures) == 1
    assert captures[0].user_message == "say hello"
    assert captures[0].assistant_message == "done"
    session.refresh()
    assert session.history[-1]["content"] == "done"
