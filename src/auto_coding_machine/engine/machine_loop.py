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

from auto_coding_machine.engine.contracts import BudgetPolicy, CancellationToken

from auto_coding_machine.common.llm_client import ContextLengthExceededError
from auto_coding_machine.common.token_utils import count_tokens_old_style as count_tokens
from auto_coding_machine.engine.hook_manager import HookManager
from auto_coding_machine.engine.request_view import RequestView
from auto_coding_machine.engine.tool_batch import ToolBatch


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
        context_selector=None,
        session_store=None,
        completion_gate=None,
        status_bar=None,
        request_view=None,
    ):
        """初始化。

        参数：
          model_fn        — 模型调用函数，signature: (messages: list) -> AgentResponse
                            传入谁，Loop 就用谁做决策。
                            CLI 传 TuiModelAdapter.call；
                            调用方传正式的 LLM 适配器；
                            测试传 mock 函数。
          tools           — 工具执行器，提供 execute(ToolCall) -> ToolResult
          permission      — 权限管理器，提供 check(ToolCall) -> PermissionDecision
          guard           — 守卫管理器，提供 should_stop(...) -> bool
          budget          — 预算策略，包含 max_turns / timeout 等
          final_verifier  — 完成判定函数，signature: (messages, response) -> bool
          hooks           — 钩子管理器，可选。不传就用空壳（不报错）。
          context_manager — 上下文管理器，可选。不传就不压缩。
          context_selector— 上下文选择器，可选。只改变本次模型请求视图，
                            不修改 messages，也不写入 Session。
          session_store   — Session 存储器，可选（SessionStore 实例）。
                            传了的话，循环中产生的每条新消息
                            （助手回复 / 工具结果）都会同步写盘。
                            不传行为和以前完全一样。
          completion_gate — 完成证据门，可选（CompletionGate 协议对象）。
                            模型给出最终回答时调用其 evaluate(candidate) 做裁决：
                            accept → 回答生效；continue → 退回验证；fail → 带未验证
                            标记交付。不传则模型说 done 即完成，行为同旧版。
          status_bar     — 可选的状态消息构造器。每次模型调用前接收一份执行状态，
                             返回只存在于本次请求中的消息；不会写入 messages 或 Session。
          request_view  — 可选的请求视图构造器。未传时从 context_selector 和
                             status_bar 创建默认 RequestView。
        """
        self.model_fn = model_fn
        self.tools = tools
        self.permission = permission
        self.guard = guard
        self.budget = budget
        self.final_verifier = final_verifier
        self.hooks = hooks or HookManager()
        self.context_manager = context_manager
        self.context_selector = context_selector
        self.session_store = session_store
        self.completion_gate = completion_gate
        self.status_bar = status_bar
        self.request_view = request_view or RequestView(
            context_selector=context_selector,
            status_bar=status_bar,
        )
        self._status_active = False
        self._status_goal = ""
        self._status_tool_calls = 0
        self._status_last_tool = None
        self._status_last_failure = None
        if self.status_bar is not None:
            self.hooks.on_event(self._on_status_event)

    def start_task(self, messages=None) -> None:
        """开始一个新的状态栏任务；权限暂停后的 resume 不会调用它。"""
        if self.status_bar is None:
            return
        self._status_active = True
        self._status_goal = self._latest_user_message(messages or [])
        self._status_tool_calls = 0
        self._status_last_tool = None
        self._status_last_failure = None

    def _finish_status_task(self) -> None:
        self._status_active = False

    @staticmethod
    def _latest_user_message(messages) -> str:
        for message in reversed(messages):
            if message.get("role") != "user":
                continue
            content = str(message.get("content") or "").strip()
            if content:
                return content
        return ""

    def _build_request_messages(self, messages, turn):
        """在临时请求视图末尾追加状态，不改变原始消息列表。"""
        state = {
            "goal": self._status_goal,
            "turn": turn + 1,
            "max_turns": self.budget.max_turns,
            "tool_calls": self._status_tool_calls,
            "last_tool": self._status_last_tool,
            "last_failure": self._status_last_failure,
        }
        return self.request_view.build(messages, state)

    def _on_status_event(self, event) -> None:
        """从统一事件流记录工具状态，覆盖自动和确认后的执行路径。"""
        if event.name == "pre_tool":
            self._status_tool_calls += 1
            self._status_last_tool = event.data.get("tool_name")
        elif event.name == "post_tool" and event.data.get("error"):
            name = event.data.get("tool_name") or self._status_last_tool or "unknown"
            self._status_last_failure = (
                f"{name}: {event.data.get('error_type') or 'error'}"
            )

    def _recover_from_context_overflow(self, messages, turn, request_messages=None):
        """上下文超限后的唯一一次恢复尝试：强制压缩 → 重调模型一次。

        返回（统一用 dict，调用方一眼能看懂）：
          恢复成功 → {"status": "ok", "messages": 压缩后的消息, "response": 模型回复}
          恢复失败 → {"status": "failed", "error": "人话说明"}

        三条失败路径都直接返回明确错误，绝不循环重试：
          1. 没配压缩器 —— 压缩无从谈起
          2. 压缩后 token 没减少 —— 再压也压不动，重试是浪费
          3. 重试仍然超限 —— 模型窗口就是装不下这批消息
        """
        if self.context_manager is None:
            self.hooks.fire("failed", error="context_overflow", turn=turn)
            return {"status": "failed",
                    "error": "上下文超出模型窗口，且未配置压缩器，无法自动恢复"}

        # 先量一次压缩前的 token 数，用来判断"压缩到底有没有进展"
        if request_messages is None:
            request_messages = self._build_request_messages(messages, turn)
        request_counter = getattr(self.context_manager, "count_request_tokens", count_tokens)
        tokens_before = request_counter(request_messages)
        compacted = self.context_manager.maybe_compact(messages, force=True)
        retry_messages = self._build_request_messages(compacted, turn)
        tokens_after = request_counter(retry_messages)

        if tokens_after >= tokens_before:
            self.hooks.fire("failed", error="context_overflow", turn=turn)
            return {"status": "failed",
                    "error": "上下文超出模型窗口，且强制压缩没有减少内容，无法自动恢复"}

        # 压缩有进展，重试一次（这是唯一的一次，没有第二次）
        self.hooks.fire(
            "compaction_fallback",
            kind="context_overflow_retry",
            error="上下文超限，已强制压缩后重试一次",
            turn=turn,
        )
        try:
            response = self.model_fn(retry_messages)
        except ContextLengthExceededError:
            self.hooks.fire("failed", error="context_overflow", turn=turn)
            return {"status": "failed",
                    "error": "上下文压缩后仍超出模型窗口，请用 /compact 或开新会话"}

        return {"status": "ok", "messages": compacted, "response": response}

    def _record(self, message: dict):
        """把一条新消息写进 session 文件（如果配了 session_store）。

        这就是“落盘点”：循环里每 append 一条消息就同步记一笔流水账。
        返回是否写入成功；没有 store 的调用方只使用内存消息。
        """
        if self.session_store is None:
            return True
        try:
            self.session_store.append(message)
        except OSError:
            return False
        return True

    def _storage_failure(self, turn, **result):
        """写盘失败后停止任务；工具可能已执行，不能继续或自动重试。"""
        self.hooks.fire("failed", error="session_write_failed", turn=turn)
        self._finish_status_task()
        return {"status": "failed", "error": "session_write_failed", **result}

    def _guard_check(self, messages, turn) -> dict | None:
        """Guard 检查；返回失败结果，或 None 表示可以继续。"""
        if self.guard.should_stop(messages, turn):
            self.hooks.fire("failed", error="guard_stopped", turn=turn)
            self._finish_status_task()
            return {"status": "failed", "error": "guard_stopped"}
        return None

    def _cancelled(self, turn) -> dict:
        self.hooks.fire("cancelled", message="任务已取消", turn=turn)
        self._finish_status_task()
        return {"status": "cancelled"}

    def _batch_stopped(self, batch, outcome) -> dict:
        """把批次的停止原因转成任务结果；取消和写入失败在这里发出结束事件。"""
        if outcome["status"] == "cancelled":
            return self._cancelled(batch.turn)
        if outcome["status"] == "write_failed":
            return self._storage_failure(batch.turn, tool_result=outcome["tool_result"])
        return outcome

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
          待审批：{"status": "permission_required", "batch": ToolBatch}，
                  宿主作出决定后用 resume() 继续

        大白话流程：
          1. 循环最多 max_turns 轮
          2. 每轮开始检查取消
          3. 调模型
          4. 没有 tool_call 且验证通过 -> 成功
          5. 有 tool_call -> 交给 ToolBatch 逐个处理 -> 回填 messages
          6. Guard 检查是否卡死
          7. 继续下一轮
        """
        if self.status_bar is not None and not self._status_active:
            self.start_task(messages)
        return self._loop(messages, cancel, 0)

    def resume(self, batch: ToolBatch, approved: bool, cancel: CancellationToken) -> dict:
        """把权限决定交给暂停的批次，批次完成后从下一轮继续循环。

        不重新调用模型，也不重复执行已完成的调用；轮次接着暂停时的轮次计数，
        一次任务的 max_turns 预算连续计算。
        """
        outcome = batch.resolve(approved, cancel)
        if outcome is not None:
            return self._batch_stopped(batch, outcome)
        guard_outcome = self._guard_check(batch.messages, batch.turn)
        if guard_outcome is not None:
            return guard_outcome
        return self._loop(batch.messages, cancel, batch.turn + 1)

    def _loop(self, messages, cancel, turn) -> dict:
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
                    # 摘要失败降级为确定性摘录时，向 CLI 发一次警告，
                    # 让用户知道"这次摘要是兜底摘录，不是 LLM 总结"
                    if self.context_manager.last_compaction_mode == "excerpt":
                        self.hooks.fire("compaction_fallback",
                            kind="summary_fallback",
                            error=self.context_manager.last_compaction_error,
                            turn=turn)

            # 第 1 步：检查取消
            if cancel.is_cancelled():
                return self._cancelled(turn)

            # 第 2 步：调模型（用构造时传入的 model_fn）
            # 上下文超限时的恢复策略：强制压缩一次 → 重试一次。
            # 只重试一次是硬约束：压缩没进展或二次仍超限就明确失败，
            # 否则"压缩没用还反复调模型"就是个死循环。
            request_messages = self._build_request_messages(messages, turn)
            try:
                response = self.model_fn(request_messages)
            except ContextLengthExceededError:
                outcome = self._recover_from_context_overflow(
                    messages,
                    turn,
                    request_messages=request_messages,
                )
                if outcome["status"] == "failed":
                    self._finish_status_task()
                    return outcome  # 恢复失败，返回清晰错误，不重试
                # 恢复成功：后续轮次也用压缩后的消息列表
                messages = outcome["messages"]
                response = outcome["response"]

            # 同步模型请求期间也可能收到取消；返回后先检查，再处理回复。
            if cancel.is_cancelled():
                return self._cancelled(turn)

            # 第 3 步：没有 tool_calls，检查是否完成
            if not response.tool_calls:
                # 只有明确 done 或 final_verifier 通过才算成功
                candidate_done = response.done or self.final_verifier(messages, response)

                # ---- CompletionGate 判断：模型说 done 只是"候选回答" ----
                # 有文件净变化就必须有新鲜的验证证据，否则退回去验证。
                # 注意 candidate 为空的情况不进 Gate（EC-8），
                # 按下面的 no_tool_call 失败处理。
                if (
                    candidate_done
                    and self.completion_gate is not None
                    and (response.content or "").strip()
                ):
                    decision = self.completion_gate.evaluate(response.content)

                    if decision.action == "accept":
                        # Gate 裁定的唯一最终回答。
                        # 验证 continuation 之后会复用原候选回答（附验证 footer），
                        # 模型的验证回执不允许顶替实质内容（FR-23）。
                        reply = decision.final_response
                        if not self._record({"role": "assistant", "content": reply}):
                            return self._storage_failure(turn, reply=reply)
                        self.hooks.fire("done", reply=reply, turn=turn)
                        self._finish_status_task()
                        return {"status": "success", "reply": reply}

                    if decision.action == "fail":
                        # 连续缺证据，停止重试——但候选回答 + 未验证标记照样交付，
                        # 用户等来的实质回答不能凭空消失（FR-25）
                        reply = decision.final_response
                        if not self._record({"role": "assistant", "content": reply}):
                            return self._storage_failure(turn, reply=reply)
                        self.hooks.fire("failed", error="verification_required", turn=turn)
                        self._finish_status_task()
                        return {
                            "status": "failed",
                            "error": "verification_required",
                            "reply": reply,
                        }

                    # action == "continue"：候选回答已存进 Gate，插入一条
                    # 运行时验证提示，让模型先跑测试再回答。
                    # 提示和候选回答都是内部脚手架，不写 JSONL（FR-22）。
                    messages.append({
                        "role": "user",
                        "content": decision.continuation_message,
                    })
                    self.hooks.fire(
                        "completion_rejected",
                        reason=decision.reason,
                        changed_paths=list(decision.changed_paths),
                        candidate_version=decision.candidate_version,
                        validation_version=decision.validation_version,
                        turn=turn,
                    )
                    turn += 1
                    continue

                if candidate_done:
                    # 最终回复也要落盘，不然 resume 后少最后一句
                    # 注意：content 可能为空（流式已显示但未回填），此时用空字符串落盘
                    reply_content = response.content or ""
                    if reply_content:
                        if not self._record({"role": "assistant", "content": reply_content}):
                            return self._storage_failure(turn, reply=reply_content)
                    self.hooks.fire("done", reply=reply_content, turn=turn)
                    self._finish_status_task()
                    return {"status": "success", "reply": reply_content}

                # 只是普通文本，不算成功
                if response.content:
                    if not self._record({"role": "assistant", "content": response.content}):
                        return self._storage_failure(turn, reply=response.content)
                    self.hooks.fire("need_input", reply=response.content, turn=turn)
                    self._finish_status_task()
                    return {"status": "need_input", "reply": response.content}

                # 既没 tool_call 也没 content，算失败
                self.hooks.fire("failed", error="no_tool_call", turn=turn)
                self._finish_status_task()
                return {"status": "failed", "error": "no_tool_call"}

            # 第 4 步：有 tool_calls，先追加 assistant message
            call_message = response.to_message()
            messages.append(call_message)
            if not self._record(call_message):
                return self._storage_failure(turn)

            # 第 5 步：逐个执行工具；每个调用都有回执或明确的未执行原因
            batch = ToolBatch(
                response.tool_calls, turn=turn, messages=messages,
                tools=self.tools, permission=self.permission,
                hooks=self.hooks, record=self._record,
            )
            outcome = batch.run(cancel)
            if outcome is not None:
                return self._batch_stopped(batch, outcome)

            # 第 6 步：Guard 检查（Phase 2 先简单实现，Phase 3 再补完整）
            guard_outcome = self._guard_check(messages, turn)
            if guard_outcome is not None:
                return guard_outcome

            turn += 1

        # 超过 max_turns。但若 Gate 里还存着候选回答，先交付它——
        # 用户已经等来一版实质回答，不能只甩一个冷冰冰的 max_turns（FR-26）
        if self.completion_gate is not None:
            pending = self.completion_gate.take_pending_final()
            if pending is not None:
                if not self._record({"role": "assistant", "content": pending}):
                    return self._storage_failure(turn, reply=pending)
                self.hooks.fire("failed", error="max_turns", turn=turn)
                self._finish_status_task()
                return {"status": "failed", "error": "max_turns", "reply": pending}
        self.hooks.fire("failed", error="max_turns", turn=turn)
        self._finish_status_task()
        return {"status": "failed", "error": "max_turns"}
