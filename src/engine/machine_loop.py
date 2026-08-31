"""Agent 核心循环：最简 while-loop。

【这文件是干什么的】
  Claude Code 论文的核心结论：
    "核心循环是一个简单的 while-loop，大部分复杂度在外围系统。"

  这个文件就是那个"简单 while-loop"：
    1. 调模型
    2. 拿到 ToolCall
    3. 检查权限
    4. 执行工具
    5. 得到 ToolResult
    6. 塞回 messages
    7. 继续 or 结束

【重要边界】
  - Loop 不关心具体工具实现（read_file 怎么读）
  - Loop 不关心权限细节（哪些允许哪些拒绝）
  - Loop 只认统一契约：ToolCall / ToolResult / PermissionDecision

【当前阶段】
  Phase 2 先用 mock LLM，不接真模型。
  目标是跑通闭环：ToolCall -> execute -> ToolResult -> append message。

【谁会用】
  Runtime Factory
  或者 CLI 里的 REPL
"""

from src.engine.contracts import (
    AgentResponse,
    BudgetPolicy,
    CancellationToken,
    PermissionDecision,
    ToolCall,
    ToolResult,
)

import time

from src.engine.hook_manager import HookManager


class MachineLoop:
    """AutoCoding Machine 核心循环。

    用法例子：
        loop = MachineLoop(
            model_fn=my_llm_adapter.call,   # 传入模型调用函数
            tools=coding_tools,
            permission=permission_mgr,
            guard=guard_mgr,
            budget=BudgetPolicy(max_turns=50),
            final_verifier=lambda msgs, resp: resp.done,
            hooks=hooks,
        )
        result = loop.run(messages, cancel_token)
    """

    def __init__(
        self,
        model_fn,
        tools,
        permission,
        guard,
        budget: BudgetPolicy,
        final_verifier,
        hooks: HookManager | None = None,
        context_manager=None,
        session_store=None,
    ):
        """初始化。

        参数：
          model_fn        — 模型调用函数，signature: (messages: list) -> AgentResponse
                            传入谁，Loop 就用谁做决策。
                            CLI 传 SimpleLLMAdapter.call；
                            调用方传正式的 LLM 适配器；
                            测试传 mock 函数。
          tools           — 工具执行器，提供 execute(ToolCall) -> ToolResult
          permission      — 权限管理器，提供 check(ToolCall) -> PermissionDecision
          guard           — 守卫管理器，提供 should_stop(...) -> bool
          budget          — 预算策略，包含 max_turns / timeout 等
          final_verifier  — 完成判定函数，signature: (messages, response) -> bool
          hooks           — 钩子管理器，可选。不传就用空壳（不报错）。
          context_manager — 上下文管理器，可选。不传就不压缩。
          session_store   — Session 存储器，可选（SessionStore 实例）。
                            传了的话，循环中产生的每条新消息
                            （助手回复 / 工具结果）都会同步写盘。
                            不传行为和以前完全一样。
        """
        self.model_fn = model_fn
        self.tools = tools
        self.permission = permission
        self.guard = guard
        self.budget = budget
        self.final_verifier = final_verifier
        self.hooks = hooks or HookManager()
        self.context_manager = context_manager
        self.session_store = session_store

    def _record(self, message: dict):
        """把一条新消息写进 session 文件（如果配了 session_store）。

        这就是“落盘点”：循环里每 append 一条消息就同步记一笔流水账。
        写盘失败不能影响主流程（比如磁盘满了），所以吞掉异常。
        """
        if self.session_store is None:
            return
        try:
            self.session_store.append(message)
        except OSError:
            # 磁盘问题不阻断任务：大不了这条没存上，对话继续
            pass

    def run(self, messages: list, cancel: CancellationToken) -> dict:
        """运行核心循环。

        参数：
          messages — 初始对话历史（list of dict）
          cancel   — 取消令牌

        返回：
          成功：{"status": "success", "reply": "..."}
          失败：{"status": "failed", "error": "..."}
          取消：{"status": "cancelled"}
          超限：{"status": "failed", "error": "max_turns"}

        大白话流程：
          1. 循环最多 max_turns 轮
          2. 每轮开始检查取消
          3. 调模型
          4. 没有 tool_call 且验证通过 -> 成功
          5. 有 tool_call -> 逐个执行 -> 回填 messages
          6. Guard 检查是否卡死
          7. 继续下一轮
        """
        turn = 0
        while turn < self.budget.max_turns:
            # 第 0 步：压缩上下文（如果提供了 context_manager）
            # 压缩真发生时 fire 一个 Hook，让 CLI / DB 能看见——
            # 不然用户被"失忆"了都不知道是压缩干的
            if self.context_manager is not None:
                before_count = len(messages)
                messages = self.context_manager.maybe_compact(messages)
                if len(messages) < before_count:
                    # dropped = 被移除的消息数；因为摘要是新插入的，所以实际 dropped = 净减少 +1
                    kept_excl_summary = len(messages) - 1  # 去掉摘要那一条
                    self.hooks.fire("compacted",
                        dropped=before_count - kept_excl_summary,
                        kept=kept_excl_summary, turn=turn)

            # 第 1 步：检查取消
            if cancel.is_cancelled():
                self.hooks.fire("cancelled", message="任务已取消", turn=turn)
                return {"status": "cancelled"}

            # 第 2 步：调模型（用构造时传入的 model_fn）
            response = self.model_fn(messages)

            # 第 3 步：没有 tool_calls，检查是否完成
            if not response.tool_calls:
                # 只有明确 done 或 final_verifier 通过才算成功
                if response.done or self.final_verifier(messages, response):
                    # 最终回复也要落盘，不然 resume 后少最后一句
                    # 注意：content 可能为空（流式已显示但未回填），此时用空字符串落盘
                    reply_content = response.content or ""
                    if reply_content:
                        self._record({"role": "assistant", "content": reply_content})
                    self.hooks.fire("done", reply=reply_content, turn=turn)
                    return {"status": "success", "reply": reply_content}

                # 只是普通文本，不算成功
                if response.content:
                    self._record({"role": "assistant", "content": response.content})
                    self.hooks.fire("need_input", reply=response.content, turn=turn)
                    return {"status": "need_input", "reply": response.content}

                # 既没 tool_call 也没 content，算失败
                self.hooks.fire("failed", error="no_tool_call", turn=turn)
                return {"status": "failed", "error": "no_tool_call"}

            # 第 4 步：有 tool_calls，先追加 assistant message
            messages.append(response.to_message())
            self._record(response.to_message())

            # 第 5 步：逐个执行工具
            for tc in response.tool_calls:
                # pre_tool 带上 tool_call_id，让前端能把"开始"和"结果"配对
                self.hooks.fire("pre_tool",
                    tool_name=tc.name, tool_call_id=tc.id,
                    arguments=tc.arguments, turn=turn)

                # 拦截检查和普通权限检查分开，避免破坏现有 PermissionManager。
                hook_decision = self.hooks.check(
                    "pre_tool",
                    tool_name=tc.name, tool_call_id=tc.id,
                    arguments=tc.arguments, turn=turn,
                )
                if hook_decision == "deny":
                    result = ToolResult(
                        tool_call_id=tc.id,
                        content="Hook 检查拒绝了这次操作",
                        error=True,
                        error_type="hook_denied",
                        retryable=False,
                    )
                    self.hooks.fire("post_tool",
                        tool_name=tc.name, tool_call_id=tc.id, error=True,
                        error_type="hook_denied", result_content=result.content,
                        result_metadata=result.metadata, duration_ms=0, turn=turn)
                elif hook_decision == "ask":
                    self.hooks.fire("permission_required",
                        tool_name=tc.name, tool_call_id=tc.id,
                        arguments=tc.arguments, turn=turn)
                    return {
                        "status": "permission_required",
                        "pending_tool_call": tc,
                        "messages": messages,
                        "turn": turn,
                    }
                else:
                    # 权限检查
                    decision = self.permission.check(tc)
                    if decision == PermissionDecision.DENY:
                        result = ToolResult(
                            tool_call_id=tc.id,
                            content="权限拒绝",
                            error=True,
                            error_type="permission",
                            retryable=False,
                        )
                        self.hooks.fire("post_tool",
                            tool_name=tc.name, tool_call_id=tc.id, error=True,
                            error_type="permission", result_content=result.content,
                            result_metadata=result.metadata, duration_ms=0, turn=turn)

                    elif decision == PermissionDecision.ASK:
                        # ASK 需要用户确认，不直接执行
                        self.hooks.fire("permission_required",
                            tool_name=tc.name, tool_call_id=tc.id,
                            arguments=tc.arguments, turn=turn)
                        # 返回暂停状态，由上层 Executor 处理恢复逻辑
                        return {
                            "status": "permission_required",
                            "pending_tool_call": tc,
                            "messages": messages,
                            "turn": turn,
                        }
                    else:
                        # AUTO：自动批准，直接执行工具
                        t0 = time.time()
                        result = self.tools.execute(tc)
                        duration_ms = int((time.time() - t0) * 1000)
                        self.hooks.fire("post_tool",
                            tool_name=tc.name, tool_call_id=tc.id,
                            error=result.error, error_type=result.error_type,
                            result_content=result.content,
                            result_metadata=result.metadata,
                            duration_ms=duration_ms, turn=turn)

                # 这里统一追加 Hook 拒绝结果；普通权限分支已在各自分支完成。
                if hook_decision == "deny":
                    messages.append(result.to_message())
                    self._record(result.to_message())
                    continue

                # 追加 tool message。三种结果都在这里回填。
                messages.append(result.to_message())
                self._record(result.to_message())

            # 第 6 步：Guard 检查（Phase 2 先简单实现，Phase 3 再补完整）
            if self.guard.should_stop(messages, turn):
                self.hooks.fire("failed", error="guard_stopped", turn=turn)
                return {"status": "failed", "error": "guard_stopped"}

            turn += 1

        # 超过 max_turns
        self.hooks.fire("failed", error="max_turns", turn=turn)
        return {"status": "failed", "error": "max_turns"}
