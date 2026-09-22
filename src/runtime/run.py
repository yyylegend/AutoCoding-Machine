"""一次用户任务的生命周期。"""

import time

from src.engine.contracts import CancellationToken, ToolResult


class AgentRun:
    """收拢单次任务的开始、权限恢复和取消。

    Session 仍由外层 Runtime 持有；这个 module 只处理一次用户输入从
    开始执行到结束之间的状态，避免 CLI 复制工具回填和 resume 细节。
    """

    def __init__(
        self,
        loop,
        tools,
        hooks,
        session_store,
        completion_gate,
        messages,
        cancel=None,
    ):
        self.loop = loop
        self.tools = tools
        self.hooks = hooks
        self.session_store = session_store
        self.completion_gate = completion_gate
        self.messages = messages
        self.cancel_token = cancel or CancellationToken()
        self.result = None
        self._started = False

    @property
    def pending_tool_call(self):
        if self.result is None:
            return None
        return self.result.get("pending_tool_call")

    def start(self) -> dict:
        """开始新任务，并重置只属于本次任务的策略状态。"""
        if self._started:
            raise RuntimeError("同一个 AgentRun 只能开始一次")
        self._started = True
        if self.completion_gate is not None:
            self.completion_gate.start_task()
        start_task = getattr(self.loop, "start_task", None)
        if start_task is not None:
            start_task(self.messages)
        self.result = self.loop.run(self.messages, self.cancel_token)
        return self.result

    def resolve_permission(self, approved: bool) -> dict:
        """处理当前 ASK 工具，并使用同一任务状态继续循环。"""
        if not self._started:
            raise RuntimeError("请先调用 start()")
        if self.result is None or self.result.get("status") != "permission_required":
            raise RuntimeError("当前任务没有等待处理的权限请求")

        tool_call = self.result["pending_tool_call"]
        if approved:
            started_at = time.time()
            tool_result = self.tools.execute(tool_call)
            duration_ms = int((time.time() - started_at) * 1000)
        else:
            tool_result = ToolResult(
                tool_call_id=tool_call.id,
                content="用户拒绝了这次操作",
                error=True,
                error_type="permission",
                retryable=False,
            )
            duration_ms = 0

        self.hooks.fire(
            "post_tool",
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            error=tool_result.error,
            error_type=tool_result.error_type,
            result_content=tool_result.content,
            result_metadata=tool_result.metadata,
            duration_ms=duration_ms,
            turn=self.result.get("turn", 0),
        )
        self.messages = self.result["messages"]
        result_message = tool_result.to_message()
        self.messages.append(result_message)
        if self.session_store is not None:
            self.session_store.append(result_message)
        self.result = self.loop.run(self.messages, self.cancel_token)
        return self.result

    def cancel(self) -> None:
        """标记当前任务取消；执行循环会在下一次检查时安全退出。"""
        self.cancel_token.cancel()
