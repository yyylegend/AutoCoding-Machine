"""Agent Runtime：统一保存组件并构造消息。"""

from src.engine import assemble


class AgentRuntime:
    """MachineLoop 外围的轻量运行时。

    它不替代 MachineLoop，只负责把公共组件和动态注入统一起来。
    """

    def __init__(self, system_prompt, loop, registry, components=None):
        self.system_prompt = system_prompt
        self.loop = loop
        self.registry = registry

        # 组件单独传入，避免为了 Runtime 改动 MachineLoop 内核。
        components = components or {}
        self.tools = components.get("tools")
        self.permission = components.get("permission")
        self.guard = components.get("guard")
        self.hooks = components.get("hooks")
        self.context_manager = components.get("context_manager")
        self.session_store = components.get("session_store")

    def build_messages(self, history, extra_injections=None):
        """按统一顺序组装消息。

        Runtime 注册的注入先放，调用方临时注入（例如 Plan Mode）后放。
        """
        injections = self.registry.build_injections()
        if extra_injections:
            injections.extend(extra_injections)
        return assemble(self.system_prompt, history, dynamic_injections=injections)

    def run(self, messages, cancel):
        """运行底层 MachineLoop。"""
        return self.loop.run(messages, cancel)
