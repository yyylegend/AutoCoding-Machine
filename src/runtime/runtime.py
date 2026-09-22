"""Agent Runtime：统一保存组件并构造消息。"""

from src.engine import assemble
from src.runtime.run import AgentRun
from src.runtime.session import AgentSession


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
        self.context_selector = components.get("context_selector")
        self.completion_gate = components.get("completion_gate")
        self.status_bar = components.get("status_bar")
        self.session_store = components.get("session_store")
        self.trace = components.get("trace")

    def build_messages(self, history, extra_injections=None):
        """按统一顺序组装消息。

        Runtime 注册的注入先放，调用方临时注入（例如 Plan Mode）后放。
        """
        injections = self.registry.build_injections()
        if extra_injections:
            injections.extend(extra_injections)
        return assemble(self.system_prompt, history, dynamic_injections=injections)

    def run(self, messages, cancel):
        """兼容入口：新调用方应通过 create_run() 管理完整任务生命周期。"""
        return self.create_run(messages, cancel).start()

    def create_run(self, messages, cancel=None):
        """创建一次用户任务的 lifecycle module。"""
        return AgentRun(
            loop=self.loop,
            tools=self.tools,
            hooks=self.hooks,
            session_store=self.session_store,
            completion_gate=self.completion_gate,
            messages=messages,
            cancel=cancel,
        )

    def create_session(self, store, history, extra_injections=None):
        """创建一段对话的会话视图与任务创建 module。"""
        return AgentSession(self, store, history, extra_injections)

    def resume(self, messages, cancel):
        """兼容旧调用方；新调用方使用 AgentRun.resolve_permission()。"""
        return self.loop.run(messages, cancel)

    def set_session_store(self, session_store):
        """切换当前 Session，并同步 Loop 与自动上下文选择器。"""
        self.session_store = session_store
        self.loop.session_store = session_store
        if self.trace is not None:
            self.trace.set_session(session_store)
        if self.context_selector is not None:
            update_session = getattr(self.context_selector, "set_current_session", None)
            if update_session is not None:
                update_session(session_store.session_id)
