"""生命周期钩子管理器。

【这文件是干什么的】
  在 Agent 循环的关键节点插入自定义逻辑，不修改循环本身。

  设计灵感：中间件模式。
  注册回调 → 循环跑到关键节点 → 触发回调 → 回调按序执行。

  第一版只做 Observer（观测），不做 Interceptor（拦截）。
  回调返回值被忽略——只能看，不能改。

【和 EventSink 的区别】
  - EventSink 是硬编码的单一出口（Loop 里写死 emit 调用）
  - HookManager 是可插拔的多个回调（外部注册，Loop 不感知）
  - HookManager 取代 EventSink——Loop 只认 hooks，不再认 event_sink

【安全铁律】
  钩子内部异常绝不阻断主流程。fire() 里每个回调包了 try/except，
  一个回调崩了，后面的照常执行，Loop 继续跑。
  但吞掉不等于不吭声：会打一行 warning 日志，不然回调一直在崩
  （比如写库失败、SSE 事件全丢）永远查不到原因。

【用法】
    hooks = HookManager()

    # 注册回调
    hooks.on("post_tool", 记录耗时到数据库)
    hooks.on("done", 发通知给用户)
    hooks.on("cancelled", 写取消日志)

    # Loop 里触发
    hooks.fire("pre_tool", tool_name="read_file", turn=3)
    hooks.fire("post_tool", tool_name="read_file", duration_ms=42, turn=3)

【谁会用】
  - MachineLoop（核心循环）：在 run() 里调 fire()
  - CLI cli_ui.py：注册终端输出回调（取代 ConsoleEventSink）
  - Runtime 调用方：注册需要的生命周期回调
  - 测试：注册 mock 回调来验证事件流
"""

from collections import defaultdict
from typing import Any, Callable

from src.common.logger import get_logger

hook_logger = get_logger("hooks")


class HookManager:
    """生命周期钩子管理器。

    两个核心方法：
      on(event, callback)  — 注册一个回调到某个事件上
      fire(event, **kwargs) — 触发某个事件的所有回调

    事件名是自由字符串，由调用方约定。当前 Loop 使用的事件：
      pre_tool           — 工具执行前（tool_name, arguments, turn）
      post_tool          — 工具执行后（tool_name, error, error_type, duration_ms, turn）
      permission_required — 工具需要用户确认（tool_name, tool_call_id, arguments, turn）
      done               — 任务完成（reply, turn）
      need_input         — 模型需要更多信息（reply, turn）
      cancelled          — 任务被取消（message, turn）
      failed             — 任务失败（error, turn）
      compacted          — 上下文被压缩（dropped, kept, turn）
      compaction_fallback — 压缩降级或恢复警告（kind, error, turn）。
                            kind=summary_fallback：摘要失败，改用确定性摘录；
                            kind=context_overflow_retry：上下文超限，强制压缩后重试一次
    """

    def __init__(self):
        # 用 defaultdict(list) 避免每次注册新事件都要 if event not in dict
        self._callbacks: dict[str, list] = defaultdict(list)
        self._check_callbacks: dict[str, list] = defaultdict(list)

    # ── 注册 ──────────────────────────────────────────

    def on(self, event: str, callback: Callable) -> None:
        """注册一个钩子回调。

        参数：
          event    — 事件名，如 "pre_tool" / "post_tool" / "done"
          callback — 回调函数。签名为 callback(**kwargs) -> None。
                     返回值被忽略（Observer 模式，只看不改）。

        可以给同一个事件注册多个回调，按注册顺序执行。
        """
        self._callbacks[event].append(callback)

    # ── 触发 ──────────────────────────────────────────

    def on_check(self, event: str, callback: Callable) -> None:
        """注册一个可阻断检查。回调返回 allow、deny 或 ask。"""
        self._check_callbacks[event].append(callback)

    def check(self, event: str, **kwargs: Any) -> str:
        """执行阻断检查，默认放行。

        检查回调异常时按安全策略拒绝，避免安全钩子失效后误放行。
        当前核心循环先处理 deny；ask 的交互恢复由上层入口后续接入。
        """
        for cb in self._check_callbacks.get(event, []):
            try:
                decision = cb(**kwargs)
            except Exception as exc:
                hook_logger.warning(
                    "Hook 检查异常（按 deny 处理）：event=%s callback=%s error=%s",
                    event, getattr(cb, "__name__", repr(cb)), exc,
                )
                return "deny"
            if decision in ("deny", "ask"):
                return decision
        return "allow"

    def fire(self, event: str, **kwargs: Any) -> None:
        """触发一个事件的所有回调。

        参数：
          event  — 事件名
          kwargs — 传给回调的键值对。不同事件传不同字段。

        回调按注册顺序执行。
        单个回调异常不影响后续回调和主流程，但会打 warning 日志留底。
        """
        for cb in self._callbacks.get(event, []):
            try:
                cb(**kwargs)
            except Exception as exc:
                # 铁律：钩子失败绝不阻断主流程。
                # 但要留一行日志，不然回调一直在崩都没人知道
                hook_logger.warning(
                    "Hook 回调异常（已忽略）：event=%s callback=%s error=%s",
                    event, getattr(cb, "__name__", repr(cb)), exc,
                )
