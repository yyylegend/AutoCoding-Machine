"""一段对话的会话视图与任务创建。"""

from src.engine.session_store import repair_dangling_tool_results


class AgentSession:
    """收拢 JSONL 历史、压缩视图和单次任务的创建。

    原始消息始终由 SessionStore 保存；base_view 只是进程内的上下文视图，
    不会回写或替换 JSONL。
    """

    def __init__(self, runtime, store, history, extra_injections=None):
        self.runtime = runtime
        self.store = store
        self._extra_injections = list(extra_injections or [])
        self._base_view = []
        self._base_count = 0
        self.history = list(history)
        self.messages = []
        self.rebuild()

    def rebuild(self):
        """按当前视图重建模型消息，适合临时注入发生变化后的刷新。"""
        self.messages = self.runtime.build_messages(
            self.history,
            extra_injections=self._extra_injections or None,
        )
        return self.messages

    def set_extra_injections(self, injections=None) -> None:
        """更新本次会话的临时注入，并重建模型消息。"""
        self._extra_injections = list(injections or [])
        self.rebuild()

    def refresh(self):
        """从原始 JSONL 重建当前视图，保留此前形成的压缩结果。"""
        full_history = self.store.load()
        self.history = self._base_view + full_history[self._base_count:]
        return self.rebuild()

    def begin_run(self, user_content: str):
        """追加用户输入、刷新视图并创建一次 AgentRun。"""
        self.store.append({"role": "user", "content": user_content})
        self.refresh()
        return self.runtime.create_run(self.messages)

    def append_message(self, message: dict) -> None:
        """追加一条需要跨会话保留的原始消息，再刷新视图。"""
        self.store.append(message)
        self.refresh()

    def clear(self) -> None:
        """从当前模型视图隐藏已有历史，但不删除 JSONL。"""
        self._base_view = []
        self._base_count = len(self.store.load())
        self.history = []
        self.rebuild()

    def compact(self):
        """压缩当前视图；返回 (changed, before_count, after_count)。"""
        before_count = len(self.history)
        context_manager = self.runtime.context_manager
        if context_manager is None:
            return False, before_count, before_count
        compacted = context_manager.maybe_compact(self.history, force=True)
        if len(compacted) >= before_count:
            return False, before_count, before_count
        self._base_view = list(compacted)
        self._base_count = len(self.store.load())
        self.history = list(self._base_view)
        self.rebuild()
        return True, before_count, len(self.history)

    def switch(self, store, history) -> None:
        """切换到同一 Profile 内的另一段原始会话。"""
        self.store = store
        self.runtime.set_session_store(store)
        self._base_view = []
        self._base_count = 0
        self.history = list(history)
        self.rebuild()

    def repair_interrupted(self) -> int:
        """补齐中断留下的工具回执，并回到 JSONL 派生的视图。"""
        repaired = repair_dangling_tool_results(self.store)
        self.refresh()
        return repaired
