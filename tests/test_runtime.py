"""Runtime 注册表和统一消息组装测试。"""

from auto_coding_machine.runtime.registry import RuntimeContext, RuntimeRegistry
from auto_coding_machine.runtime.runtime import AgentRuntime


# 兼容性导出：factory 仍可导入 MarkdownMemoryExtension。


class FakeLoop:
    def run(self, messages, cancel, **kwargs):
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
    from auto_coding_machine.runtime.factory import MarkdownMemoryExtension

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
