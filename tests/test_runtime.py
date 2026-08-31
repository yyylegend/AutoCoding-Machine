"""Runtime 注册表和统一消息组装测试。"""

from src.runtime.registry import RuntimeContext, RuntimeRegistry
from src.runtime.runtime import AgentRuntime


# 兼容性导出：factory 仍可导入 MarkdownMemoryExtension。


class FakeLoop:
    def run(self, messages, cancel):
        return {"status": "success", "message_count": len(messages)}


def test_registry_keeps_injection_order_and_values():
    context = RuntimeContext(workspace="workspace")
    registry = RuntimeRegistry(context)
    registry.register_injection_value("instructions", {"role": "system", "content": "rules"})
    registry.register_injection_value("memory", {"role": "system", "content": "memory"})

    assert registry.get_injection_names() == ["instructions", "memory"]
    assert [item["content"] for item in registry.build_injections()] == ["rules", "memory"]


def test_registry_rejects_duplicate_injection():
    registry = RuntimeRegistry(RuntimeContext(workspace="workspace"))
    registry.register_injection_value("memory", None)

    try:
        registry.register_injection_value("memory", None)
    except ValueError as exc:
        assert "memory" in str(exc)
    else:
        raise AssertionError("重复 injection 应该报错")


def test_markdown_memory_extension_accepts_provider():
    from src.runtime.factory import MarkdownMemoryExtension

    class Provider:
        def build_injection(self, context):
            return {"role": "system", "content": str(context.workspace)}

    registry = RuntimeRegistry(RuntimeContext(workspace="workspace"))

    class RuntimeStub:
        context = registry.context

        def register_injection_value(self, name, value):
            registry.register_injection_value(name, value)

    MarkdownMemoryExtension(Provider()).register(RuntimeStub())

    assert registry.build_injections()[0]["content"] == "workspace"


def test_extension_register_can_add_injection():
    class ExampleExtension:
        def register(self, runtime):
            runtime.register_injection_value(
                "example",
                {"role": "system", "content": "from extension"},
            )

    registry = RuntimeRegistry(RuntimeContext(workspace="workspace"))
    registry.use(ExampleExtension())

    assert registry.build_injections()[0]["content"] == "from extension"


def test_runtime_build_messages_keeps_history_at_end():
    registry = RuntimeRegistry(RuntimeContext(workspace="workspace"))
    registry.register_injection_value("rules", {"role": "system", "content": "rules"})
    runtime = AgentRuntime("stable system", FakeLoop(), registry)

    messages = runtime.build_messages([{"role": "user", "content": "hello"}])

    assert messages[0]["content"] == "stable system"
    assert messages[1]["content"] == "rules"
    assert messages[2]["content"] == "hello"


def test_runtime_extra_injection_is_not_persisted_in_registry():
    registry = RuntimeRegistry(RuntimeContext(workspace="workspace"))
    runtime = AgentRuntime("stable system", FakeLoop(), registry)

    messages = runtime.build_messages(
        [],
        extra_injections=[{"role": "system", "content": "temporary"}],
    )

    assert messages[-1]["content"] == "temporary"
    assert registry.get_injection_names() == []


def test_cost_command_reports_token_totals():
    from src.profiles.coding.commands.cost import handle_cost

    class FakeConsole:
        def __init__(self):
            self.lines = []

        def print(self, value):
            self.lines.append(value)

    class FakeLLM:
        total_prompt_tokens = 12
        total_completion_tokens = 8

    console = FakeConsole()
    assert handle_cost({
        "console": console,
        "theme": {"dim": "dim"},
        "llm": FakeLLM(),
    }) is True
    assert "↑12 ↓8 token" in console.lines[0]
    assert "（共 20）" in console.lines[0]


def test_display_commands_delegate_to_renderers(monkeypatch):
    from src.profiles.coding.commands import memory, prompt, sessions, skills

    calls = []
    monkeypatch.setattr(skills, "print_skills", lambda value: calls.append(("skills", value)))
    monkeypatch.setattr(sessions, "print_sessions", lambda value, current: calls.append(("sessions", value, current)))
    monkeypatch.setattr(prompt, "print_prompt_debug", lambda value: calls.append(("prompt", value)))

    assert skills.handle_skills({"skills": ["demo"]}) is True
    assert sessions.handle_sessions({"sessions": ["one"], "current_id": "one"}) is True
    assert prompt.handle_prompt({"messages": ["message"]}) is True
    assert calls == [("skills", ["demo"]), ("sessions", ["one"], "one"), ("prompt", ["message"])]


def test_help_command_calls_help_renderer(monkeypatch):
    import src.profiles.coding.commands.help as help_command

    called = []
    monkeypatch.setattr(help_command, "print_help", lambda: called.append(True))

    assert help_command.handle_help({}) is True
    assert called == [True]


def test_registry_runs_registered_command():
    registry = RuntimeRegistry(RuntimeContext(workspace="workspace"))
    seen = []

    def handler(context):
        seen.append(context["value"])

    registry.register_command("/status", "显示状态", handler)

    assert registry.get_command("/status")["description"] == "显示状态"
    assert registry.run_command("/status", {"value": 42}) is True
    assert registry.run_command("/missing", {}) is False
    assert seen == [42]
