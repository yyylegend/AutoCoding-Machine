"""一次用户任务的生命周期。"""

from auto_coding_machine.engine.contracts import CancellationToken
from auto_coding_machine.memory.external import (
    MemoryCapture,
    MemoryWriteUncertainError,
)


class AgentRun:
    """收拢单次任务的开始、权限恢复和取消。

    Session 仍由外层 Runtime 持有；这个 module 只处理一次用户输入从
    开始执行到结束之间的状态。等待审批时保存暂停的 ToolBatch，
    权限决定原样交回 MachineLoop。
    """

    def __init__(
        self,
        loop,
        session_store,
        completion_gate,
        messages,
        cancel=None,
        memory_writer=None,
        memory_scope=None,
    ):
        self.loop = loop
        self.session_store = session_store
        self.completion_gate = completion_gate
        self._messages = messages
        self.memory_writer = memory_writer
        self.memory_scope = memory_scope
        self._user_message = next((
            str(message.get("content") or "")
            for message in reversed(self._messages)
            if message.get("role") == "user"
        ), "")
        self.cancel_token = cancel or CancellationToken()
        self.result = None
        self._started = False
        self._memory_writeback_attempted = False
        self._batch = None

    def start(self) -> dict:
        """开始新任务，并重置只属于本次任务的策略状态。"""
        if self._started:
            raise RuntimeError("同一个 AgentRun 只能开始一次")
        self._started = True
        if self.completion_gate is not None:
            self.completion_gate.start_task()
        start_task = getattr(self.loop, "start_task", None)
        if start_task is not None:
            start_task(self._messages)
        return self._settle(self.loop.run(self._messages, self.cancel_token))

    def _settle(self, loop_result) -> dict:
        """把循环结果转成 Run Result；等待审批时保存暂停的批次。"""
        if loop_result.get("status") == "permission_required":
            self._batch = loop_result["batch"]
            self.result = {
                "status": "permission_required",
                "permission_request": self._batch.permission_request(),
            }
            return self.result

        self.result = loop_result
        if self.result.get("status") == "success":
            self._write_completed_turn()
        return self.result

    def _write_completed_turn(self) -> None:
        """Opt-in capture runs once after local success; it never retries. """
        if self.memory_writer is None or self._memory_writeback_attempted:
            return
        self._memory_writeback_attempted = True

        reply = self.result.get("reply")
        session_id = getattr(self.session_store, "session_id", None)
        if not self._user_message.strip() or not isinstance(reply, str) or not reply.strip() or not session_id:
            self.result["memory_writeback"] = {
                "status": "skipped",
                "reason": "missing_completed_turn_context",
            }
            return

        capture = MemoryCapture(
            scope=self.memory_scope,
            session_id=session_id,
            user_message=self._user_message,
            assistant_message=reply,
        )
        try:
            self.memory_writer.capture(capture)
        except MemoryWriteUncertainError as exc:
            self.result["memory_writeback"] = {
                "status": "unknown",
                "error_type": type(exc).__name__,
            }
        except Exception as exc:
            self.result["memory_writeback"] = {
                "status": "failed",
                "error_type": type(exc).__name__,
            }
        else:
            self.result["memory_writeback"] = {"status": "saved"}

    def resolve_permission(self, approved: bool) -> dict:
        """把权限决定交给暂停的批次，并使用同一任务状态继续循环。

        每个权限决定只消费一次；重复提交抛 RuntimeError。
        取消是协作式取消：等待审批期间取消后，即使批准到达也不执行工具，
        只补明确的未执行回执；已发生的副作用不会被撤回。
        """
        if not self._started:
            raise RuntimeError("请先调用 start()")
        if self._batch is None:
            raise RuntimeError("当前任务没有等待处理的权限请求")
        batch, self._batch = self._batch, None
        return self._settle(self.loop.resume(batch, approved, self.cancel_token))

    def cancel(self) -> None:
        """标记当前任务取消；执行循环会在下一次检查时安全退出。"""
        self.cancel_token.cancel()
