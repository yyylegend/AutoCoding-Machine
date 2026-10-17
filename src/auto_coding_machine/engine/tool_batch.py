"""一次模型回复中的工具调用批次（Tool Batch）。"""

import time

from auto_coding_machine.engine.contracts import PermissionDecision, ToolResult
from auto_coding_machine.engine.events import make_permission_request


class ToolBatch:
    """按顺序处理一次模型回复里的全部工具调用。

    每个调用都有回执：执行结果、拒绝原因或「未执行」原因。
    某个调用需要审批时，批次停在这个调用上；宿主作出决定后调用 resolve()，
    批次从同一位置继续，已处理的调用不会重复执行。

    run() 与 resolve() 返回 None 表示每个调用都已有回执；返回 dict 表示批次停止：
      - permission_required：等待审批，dict["batch"] 就是本批次；
      - cancelled：协作式取消，未执行的调用已补回执；
      - write_failed：回执写入失败，工具可能已执行，不再继续。
    任务结束事件（cancelled / failed）由 MachineLoop 统一发出。
    """

    def __init__(self, calls, *, turn, messages, tools, permission, hooks, record):
        self.turn = turn
        self.messages = messages
        self._calls = list(calls)
        self._index = 0
        self._paused = False
        self._tools = tools
        self._permission = permission
        self._hooks = hooks
        self._record = record

    def permission_request(self) -> dict:
        """当前等待审批的调用的安全视图。"""
        call = self._calls[self._index]
        return make_permission_request(call.name, call.id, call.arguments, self.turn)

    def run(self, cancel) -> dict | None:
        """从当前位置继续处理剩余调用。"""
        while self._index < len(self._calls):
            # 协作式取消：调用之间检查令牌，不再执行后续调用。
            if cancel.is_cancelled():
                return self._cancel_rest()
            call = self._calls[self._index]
            checked = self._check_and_execute(call)
            if checked is None:
                self._paused = True
                return {"status": "permission_required", "batch": self}
            failure = self._receipt(call, *checked)
            if failure is not None:
                return failure
        if cancel.is_cancelled():
            return self._cancel_rest()
        return None

    def resolve(self, approved: bool, cancel) -> dict | None:
        """处理等待审批的调用，再继续同批剩余调用。

        取消后到达的批准也不执行工具，只补「未执行」回执。
        """
        if not self._paused:
            raise RuntimeError("当前批次没有等待处理的权限请求")
        self._paused = False
        call = self._calls[self._index]
        self._hooks.fire(
            "permission_resolved",
            tool_name=call.name, tool_call_id=call.id,
            decision="approved" if approved else "denied", turn=self.turn,
        )
        if cancel.is_cancelled():
            result, duration_ms = _not_executed(call), 0
        elif approved:
            result, duration_ms = self._execute(call)
        else:
            result, duration_ms = ToolResult(
                tool_call_id=call.id, content="用户拒绝了这次操作",
                error=True, error_type="permission",
            ), 0
        failure = self._receipt(call, result, duration_ms)
        if failure is not None:
            return failure
        return self.run(cancel)

    def _check_and_execute(self, call):
        """检查并执行单个调用；返回 (ToolResult, duration_ms)，需要审批时返回 None。"""
        # pre_tool 带上 tool_call_id，让前端能把"开始"和"结果"配对
        self._hooks.fire("pre_tool",
            tool_name=call.name, tool_call_id=call.id,
            arguments=call.arguments, turn=self.turn)

        # 拦截检查和普通权限检查分开，避免破坏现有 PermissionManager。
        hook_decision = self._hooks.check(
            "pre_tool",
            tool_name=call.name, tool_call_id=call.id,
            arguments=call.arguments, turn=self.turn,
        )
        if hook_decision == "deny":
            return ToolResult(
                tool_call_id=call.id, content="Hook 检查拒绝了这次操作",
                error=True, error_type="hook_denied",
            ), 0
        if hook_decision != "ask":
            decision = self._permission.check(call)
            if decision == PermissionDecision.DENY:
                return ToolResult(
                    tool_call_id=call.id, content="权限拒绝",
                    error=True, error_type="permission",
                ), 0
            if decision == PermissionDecision.AUTO:
                return self._execute(call)
        self._hooks.fire("permission_required",
            tool_name=call.name, tool_call_id=call.id,
            arguments=call.arguments, turn=self.turn)
        return None

    def _execute(self, call):
        started_at = time.time()
        result = self._tools.execute(call)
        return result, int((time.time() - started_at) * 1000)

    def _receipt(self, call, result, duration_ms) -> dict | None:
        """发出 post_tool、追加并写入回执；写入失败时返回 write_failed。"""
        self._hooks.fire("post_tool",
            tool_name=call.name, tool_call_id=call.id,
            error=result.error, error_type=result.error_type,
            result_content=result.content,
            result_metadata=result.metadata,
            duration_ms=duration_ms, turn=self.turn)
        message = result.to_message()
        self.messages.append(message)
        self._index += 1
        if not self._record(message):
            # 工具可能已有副作用。停止批次并返回回执，避免继续执行或重试。
            return {"status": "write_failed", "tool_result": message}
        return None

    def _cancel_rest(self) -> dict:
        """取消后为尚未执行的调用补「未执行」回执，保持工具回执历史完整。"""
        while self._index < len(self._calls):
            call = self._calls[self._index]
            failure = self._receipt(call, _not_executed(call), 0)
            if failure is not None:
                return failure
        return {"status": "cancelled"}


def _not_executed(call) -> ToolResult:
    return ToolResult(
        tool_call_id=call.id, content="任务已取消，本次调用未执行",
        error=True, error_type="cancelled",
    )
