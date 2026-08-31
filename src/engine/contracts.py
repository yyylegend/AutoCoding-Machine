"""通用 Agent 契约（contracts）。

【这文件是干什么的】
  它不负责真正做事，只负责规定“大家怎么说话”。

  可以把它想成快递系统的单据格式：
    ToolCall       = 工单：模型说“我要做什么”
    ToolResult     = 回执：工具说“做完了，结果是什么 / 为什么失败”
    AgentResponse  = 模型这一轮的完整回复

  有了统一格式后：
    1. MachineLoop 才能稳定地把结果塞回上下文
    2. 模型才能根据成功/失败结果继续推理下一步
    3. 权限、重试、日志、SSE 也能读同一份结果

【重要边界】
  - contracts 只定义数据格式，不自动回传错误
  - 真正“错误回传”是 Loop 做的：
      工具失败
        → 生成 ToolResult(error=True, error_type=...)
        → 转成 tool message
        → 塞回 messages
        → 下一轮模型再看
  - 同一份 ToolResult 不只给模型看，也给系统看：
      Permission / Guard / EventSink / 数据库 都会用

【和现有 GUI 的关系】
  phase2/actions.py 里的 AgentAction 是 GUI 专用动作契约。
  本文件是更通用的 ToolCall / ToolResult 契约，
  供 coding / browser / desktop 等 Profile 共用。

【谁会用】
  - src/engine/machine_loop.py：核心循环（已实现）
  - src/profiles/coding/tools/*：只读代码工具（已实现）
  - src/profiles/browser/*（计划）：包装现有 graph.execute()
  - tests/test_engine_contracts.py：基础测试
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


@dataclass
class ToolCall:
    """模型下的工单：我要调用哪个工具、参数是什么。

    大白话例子：
      ToolCall(
          id="call_1",
          name="read_file",
          arguments={"path": "src/main.py"},
      )
      意思是：请读取 src/main.py。

    字段：
      id         — 工单编号。后面 ToolResult 会用同一个 id 对上
      name       — 工具名，比如 read_file / grep / edit_file
      arguments  — 参数字典，不同工具字段不同

    注意：
      这里只表示“模型想做什么”，还不代表已经获准执行。
      真正执行前还要过 PermissionManager。
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class AgentResponse:
    """模型这一轮的完整回复。

    为什么需要它：
      模型一轮输出可能同时包含：
        1. 普通文字 content
        2. 工具调用 tool_calls
        3. 两者都有

      所以不能只看 content 就结束任务。

    字段：
      content     — 模型说的话（可能为空）
      tool_calls  — 本轮要执行的工具列表（可为空）
      done        — 模型是否明确表示任务完成

    结束规则（很重要）：
      - content 有文字 ≠ 任务成功
      - 只有 done=True，或 Profile 的 final_verifier 判定通过，才算成功
      - 没有 tool_calls 且只是在解释/要更多信息时，可能是 need_input，不是 success

    to_message()：
      把当前对象转成 OpenAI-compatible 的 assistant message，
      方便塞回 messages 历史。
    """

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    done: bool = False

    def to_message(self) -> dict[str, Any]:
        """转成 OpenAI-compatible assistant message。

        返回大致长这样：
          {
            "role": "assistant",
            "content": "我先读文件",
            "tool_calls": [
              {
                "id": "call_1",
                "type": "function",
                "function": {
                  "name": "read_file",
                  "arguments": "{\"path\": \"src/main.py\"}"
                }
              }
            ]
          }

        如果没有 tool_calls，就只返回 role + content。
        """
        message: dict[str, Any] = {
            "role": "assistant",
            "content": self.content or None,
        }
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": _dump_arguments(call.arguments),
                    },
                }
                for call in self.tool_calls
            ]
        return message


@dataclass
class ToolResult:
    """工具执行后的回执。

    大白话例子（成功）：
      ToolResult(
          tool_call_id="call_1",
          content="文件内容...",
          error=False,
      )

    大白话例子（失败）：
      ToolResult(
          tool_call_id="call_1",
          content="路径越界，禁止读取",
          error=True,
          error_type="permission",
          retryable=False,
      )

    字段：
      tool_call_id — 对应哪个 ToolCall
      content      — 给模型看的结果文本
      error        — 是否失败
      error_type   — 失败分类，方便系统决策
      retryable    — 是否值得自动重试
      metadata     — 额外信息，比如 duration_ms / exit_code / child_run_id

    建议的 error_type：
      permission    — 没权限（通常不该盲目重试）
      timeout       — 超时（可能可重试）
      invalid_args  — 参数写错（通常改参数后再试）
      execution     — 执行中出错（看情况）
      environment   — 网络/环境问题（看情况）
      cancelled     — 用户取消

    为什么不只返回字符串：
      因为同一份结果要服务多方：
        - 模型：根据 content 决定下一步
        - Loop：根据 error / retryable 决定是否重试
        - Guard：判断是不是卡死
        - EventSink / DB：记录失败原因

    to_message()：
      转成 OpenAI-compatible 的 tool message，
      必须带 tool_call_id，才能和前面的 assistant tool_calls 配对。
    """

    tool_call_id: str
    content: str
    error: bool = False
    error_type: str | None = None
    retryable: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_message(self) -> dict[str, Any]:
        """转成 OpenAI-compatible tool message。

        返回大致长这样：
          {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": "文件内容..."
          }

        注意：
          assistant(tool_calls) 和 tool(result) 必须成对出现。
          压缩上下文时也不能切断这对消息。
        """
        return {
            "role": "tool",
            "tool_call_id": self.tool_call_id,
            "content": self.content,
        }


class PermissionDecision(str, Enum):
    """权限裁决结果。

    AUTO — 绿灯，直接执行
    ASK  — 黄灯，先问用户
    DENY — 红灯，直接拒绝

    例子：
      read_file / grep     -> AUTO
      write_file / run_test -> ASK
      危险命令 / 路径越界   -> DENY

    注意：
      这是“执行前门禁”，不是 Prompt 里的建议。
      DENY 时通常直接生成 ToolResult(error_type="permission")，
      不要假装工具已经执行成功。
    """

    AUTO = "auto"
    ASK = "ask"
    DENY = "deny"


@dataclass
class BudgetPolicy:
    """任务预算，防止 Agent 无限跑。

    字段：
      max_turns         — 最多跑多少轮工具调用
      timeout_per_tool  — 单个工具最长执行秒数
      max_output_chars  — 单次工具输出最多多少字符
      max_total_tokens  — 整个会话上下文 token 上限（后续 ContextManager 用）

    大白话：
      不能无限聊天，不能无限读超大文件，也不能一个工具卡死不结束。
    """

    max_turns: int = 50
    timeout_per_tool: int = 120
    max_output_chars: int = 50_000
    max_total_tokens: int = 100_000


class CancellationToken:
    """取消令牌：停车信号。

    用法：
      1. 调用方收到取消请求后调用 token.cancel()
      2. MachineLoop 每轮开始、工具执行前后检查 token.is_cancelled()
      3. 一旦为 True，尽快安全退出

    为什么需要它：
      用户取消任务时，不能等模型把整轮工具都跑完。
      Loop 需要一个统一、轻量的停止信号。
    """

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        """标记为已取消。"""
        self._cancelled = True

    def is_cancelled(self) -> bool:
        """是否已经取消。"""
        return self._cancelled


class EventSink:
    """【已废弃】事件出口接口。被 HookManager 取代，不要在新代码里用。

    废弃原因：硬编码的单一出口，扩展要改 Loop；
    HookManager 是可插拔的多回调，外部注册，Loop 不感知。
    类本身先保留（避免打破旧 import），已从 src.engine 公开出口移除。
    """

    def emit(
        self,
        event_type: str,
        message: str,
        step: int | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        """输出一条事件。

        参数：
          event_type — 事件类型，如 step_start / tool / done / error
          message    — 给人看的文字
          step       — 可选，对应第几步
          data       — 可选，附加结构化数据
        """
        raise NotImplementedError


def _dump_arguments(arguments: dict[str, Any]) -> str:
    """把 arguments 转成 JSON 字符串。

    OpenAI tool-call 协议要求 function.arguments 是字符串，不是 dict。
    这里统一处理，避免每个调用点自己 json.dumps。
    """
    import json

    return json.dumps(arguments, ensure_ascii=False)
