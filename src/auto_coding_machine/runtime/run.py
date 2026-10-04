"""一次用户任务的生命周期。"""

import time

from auto_coding_machine.engine.contracts import CancellationToken, ToolResult
from auto_coding_machine.engine.events import make_permission_request
from auto_coding_machine.memory.external import (
    MemoryCapture,
    MemoryWriteUncertainError,
)


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
        memory_writer=None,
        memory_scope=None,
    ):
        self.loop = loop
        self.tools = tools
        self.hooks = hooks
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
        self._pending_tool_call = None
        self._pending_turn = None

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
        return self._run_loop()

    def _run_loop(self) -> dict:
        loop_result = self.loop.run(self._messages, self.cancel_token)
        if loop_result.get("status") == "permission_required":
            self._pending_tool_call = loop_result["pending_tool_call"]
            self._messages = loop_result["messages"]
            turn = loop_result.get("turn", 0)
            self._pending_turn = turn
            self.result = {
                "status": "permission_required",
                "permission_request": make_permission_request(
                    self._pending_tool_call.name,
                    self._pending_tool_call.id,
                    self._pending_tool_call.arguments,
                    turn,
                ),
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
        """处理当前 ASK 工具，并使用同一任务状态继续循环。"""
        if not self._started:
            raise RuntimeError("请先调用 start()")
        if self.result is None or self.result.get("status") != "permission_required":
            raise RuntimeError("当前任务没有等待处理的权限请求")

        tool_call = self._pending_tool_call
        if tool_call is None:
            raise RuntimeError("当前任务没有待处理的权限请求")
        turn = self._pending_turn
        self._pending_tool_call = None
        self._pending_turn = None
        self.hooks.fire(
            "permission_resolved",
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            decision="approved" if approved else "denied",
            turn=turn,
        )
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
            turn=turn,
        )
        result_message = tool_result.to_message()
        self._messages.append(result_message)
        if self.session_store is not None:
            try:
                self.session_store.append(result_message)
            except OSError:
                # 工具可能已有副作用。停止本次任务并返回结果，避免继续执行或重试。
                self.result = {
                    "status": "failed",
                    "error": "session_write_failed",
                    "tool_result": result_message,
                }
                self.hooks.fire("failed", error="session_write_failed", turn=turn)
                return self.result
        return self._run_loop()

    def cancel(self) -> None:
        """标记当前任务取消；执行循环会在下一次检查时安全退出。"""
        self.cancel_token.cancel()
