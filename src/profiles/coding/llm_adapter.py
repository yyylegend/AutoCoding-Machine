"""LLM 适配器：把 chat() 包装成 MachineLoop 需要的格式。

【这文件是干什么的】
  MachineLoop 需要一个"调模型"的函数，输入消息列表，返回 AgentResponse。
  但 src/common/llm_client.py 的 chat() 返回的是原始 dict，需要转换。

  这个文件就是做这个转换的"中间人"。

【为什么有两个类】
  Runtime 需要无 UI 的基础 adapter，CLI 需要流式终端渲染。

  所以：
    BaseCodingAdapter    — 共享的核心逻辑（调 chat、解析 tool_calls、判断 done）
    StreamingAdapter     — 继承基类，加上流式 + 指标（CLI 专用）

【完成判断规则】（两个类共用，改一处就够）
  按 tool-calling 协议的自然语义：
    模型这轮带 tool_calls → 还想干活，done=False，继续循环
    模型这轮没带 tool_calls、只输出文本 → 这就是最终回复，done=True
    什么都没输出 → 异常，done=False，交给 Loop 按 no_tool_call 处理
  不再要求 "## 总结" 之类的文字标记（闲聊也被逼出总结，不优雅）。

【谁会用】
  - cli.py 用 StreamingAdapter
  - Runtime 可用 BaseCodingAdapter
"""

import json

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from src.common.llm_client import ContextLengthExceededError, chat, chat_stream
from src.config.settings import settings
from src.engine.contracts import AgentResponse, ToolCall


class BaseCodingAdapter:
    """基础 LLM 适配器（后台模式，不需要终端显示）。

    干什么：
      1. 调 chat() 发请求给 LLM
      2. 把返回的原始 dict 转成 ToolCall 列表
      3. 判断模型有没有完成任务（没有 tool_calls 且有回复 = 完成）
      4. 打包成 AgentResponse 返回给 MachineLoop

    用法：
      adapter = BaseCodingAdapter(tools_schemas)
      response = adapter.call(messages)
    """

    def __init__(self, tools_schemas: list, on_token=None):
        """初始化。

        参数：
          tools_schemas - OpenAI 格式的工具定义列表（传给 chat() 的 tools 参数）
          on_token      - 可选的流式回调，签名 on_token(piece: str)。
                          传了就走流式（chat_stream），每个 token 增量回调一次；
                          不传（默认 None）走非流式（chat），和原来一样。

        为什么加这个参数：
          原来只有 CLI 的 StreamingAdapter 能流式，而且流式只渲染到终端。
          调用方也可能需要纯流式回调，因此基类保留无 UI 的流式入口。
        """
        self.tools_schemas = tools_schemas
        # 外部注入的 token 回调。None = 非流式。
        self.on_token = on_token

        # 读 Coding Agent 专用的 LLM 配置
        # 如果 .env 没设 CODING_LLM_*，settings 会自动沿用 LLM_* 配置
        self.model = settings.CODING_LLM_MODEL
        self.base_url = settings.CODING_LLM_BASE_URL
        self.api_key = settings.CODING_LLM_API_KEY
        self.auth_type = settings.CODING_LLM_AUTH_TYPE
        self.max_tokens = settings.CODING_LLM_MAX_TOKENS
        self.timeout = settings.CODING_LLM_TIMEOUT_SEC

    def call(self, messages: list) -> AgentResponse:
        """调用 LLM，返回统一格式。

        参数：
          messages - 完整的消息列表（system + 历史）

        返回：
          AgentResponse(content=模型说的话, tool_calls=[...], done=是否完成)

        说明：
          on_token 不为 None 时走流式（chat_stream），边生成边回调；
          否则走非流式（chat），一次拿完整结果。
          两种方式返回的 dict 结构一样，_parse_result 都能处理。
        """
        if self.on_token is not None:
            # 流式：每个 token 增量回调一次 on_token
            result = chat_stream(
                messages, tools=self.tools_schemas,
                model=self.model, base_url=self.base_url,
                api_key=self.api_key, auth_type=self.auth_type,
                max_tokens=self.max_tokens, timeout=self.timeout,
                on_token=self.on_token,
            )
        else:
            # 非流式：一次拿完整结果
            result = chat(
                messages, tools=self.tools_schemas,
                model=self.model, base_url=self.base_url,
                api_key=self.api_key, auth_type=self.auth_type,
                max_tokens=self.max_tokens, timeout=self.timeout,
            )

        # 解析结果（流式和非流式共用）
        return self._parse_result(result)

    def _parse_result(self, result: dict) -> AgentResponse:
        """把 chat() 返回的原始 dict 转成 AgentResponse。

        这是基础 adapter 与 CLI adapter 的共享解析逻辑。
        改完成判断规则只需要改这一个方法。
        """
        content = result.get("content") or ""
        tool_calls_raw = result.get("tool_calls") or []

        # 转成 ToolCall 列表（统一契约格式）
        tool_calls = []
        for tc in tool_calls_raw:
            tool_calls.append(ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=self._parse_arguments(tc["function"]["arguments"]),
            ))

        # ★ 完成判断 ★
        # 按 tool-calling 协议：模型这轮不再调工具、又说了话，
        # 就是它的最终回复（闲聊/答复/总结都一样），不需要文字标记。
        # 既没说话也没调工具属于异常，交给 Loop 按 no_tool_call 处理。
        done = len(tool_calls) == 0 and content.strip() != ""

        return AgentResponse(content=content, tool_calls=tool_calls, done=done)

    def _parse_arguments(self, arguments):
        """把 arguments 解析成 dict。

        LLM 返回的 arguments 可能是 JSON 字符串，也可能已经是 dict 了。
        这里统一处理。
        """
        if isinstance(arguments, dict):
            return arguments
        try:
            return json.loads(arguments)
        except Exception:
            return {}


class StreamingAdapter(BaseCodingAdapter):
    """流式 LLM 适配器（CLI 终端专用）。

    在基类基础上加了：
      1. 流式输出：边生成边在终端渲染 Markdown（代码块自动高亮）
      2. 可观测指标：首字延迟（TTFT）、token 消耗
      3. 回退机制：流式失败时自动切非流式

    对 MachineLoop 完全透明：仍然返回完整的 AgentResponse。
    """

    def __init__(self, tools_schemas: list, console: Console, theme: dict,
                 publish_gate=None):
        """初始化。

        参数：
          tools_schemas — 工具定义列表
          console       — rich Console 实例（用来渲染流式输出）
          theme         — THEME 颜色字典（用来给 Panel 上色）
          publish_gate  — 可选的完成证据门（CompletionGate）。
                          传了的话，每次调模型前问它 should_publish_stream()：
                          门开着（有未验证修改 / 有候选在等验证）就静默缓冲，
                          不创建持久回答面板；门关着才正常流式展示。
                          不传 = 永远展示，行为同旧版。
        """
        super().__init__(tools_schemas)
        self.console = console
        self.theme = theme
        self.publish_gate = publish_gate

        # 流式开关（测试时可以关掉）
        self.stream = True

        # 可观测指标（每轮任务前调 reset_metrics() 清零）
        self.last_streamed = False        # 最终回答是否已持久展示给用户（静默缓冲的轮次不算）
        self.last_ttft_ms = None          # 最后一轮的首字延迟（毫秒）
        self.last_prompt_tokens = 0       # 最后一次调用的 prompt tokens（服务端精确值）
        self.total_prompt_tokens = 0      # 本任务累计输入 token
        self.total_completion_tokens = 0  # 本任务累计输出 token

    def reset_metrics(self):
        """每轮用户任务开始前调用，清零可观测指标。"""
        self.last_streamed = False
        self.last_ttft_ms = None
        self.last_prompt_tokens = 0
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

    def _should_publish(self) -> bool:
        """本轮回答该不该实时展示。

        问完成证据门的 should_publish_stream()；没配门就永远展示。
        """
        if self.publish_gate is None:
            return True
        return self.publish_gate.should_publish_stream()

    def call(self, messages: list) -> AgentResponse:
        """调用 LLM（流式优先，失败回退非流式）。

        流式时分两种展示策略（由完成证据门决定）：
          publish=True  — 边生成边用 rich Live 增量渲染 Markdown 面板；
          publish=False — 静默缓冲（token 照收，面板不建）。
                          因为这时候的回答大概率会被 Gate 打回去要求验证，
                          先显示给用户，等会儿又显示"正式版"，一份回答看两遍。

        同时记录首字延迟和 token 消耗供状态栏显示。
        """
        self.last_streamed = False
        publish = self._should_publish()   # 生成开始前先定展示策略
        buffer = []  # 累积流式 content 片段

        if self.stream:
            try:
                result = self._call_streaming(messages, buffer, publish=publish)
                # last_streamed 语义 = "最终回答已经持久展示"（FR-31）。
                # 静默缓冲的轮次不算——那部分内容要么被 Gate 替换，
                # 要么以正式版面目重新提交，不能让它误抑制最终展示。
                self.last_streamed = publish and bool(buffer)
            except ContextLengthExceededError:
                # 上下文超限必须交给 MachineLoop 先压缩再重试。
                # 不能按普通流式故障直接拿同一批消息做非流式重发。
                raise
            except Exception as exc:
                # 4xx 客户端错误（内容被拒等）：重发必然再错，直接抛出
                import requests as _requests
                if isinstance(exc, _requests.HTTPError) and exc.response is not None \
                        and 400 <= exc.response.status_code < 500:
                    raise
                # 其它错误（服务端不支持流式 / 网络问题）→ 回退非流式
                result = chat(
                    messages, tools=self.tools_schemas,
                    model=self.model, base_url=self.base_url,
                    api_key=self.api_key, auth_type=self.auth_type,
                    max_tokens=self.max_tokens, timeout=self.timeout,
                )
                self.last_streamed = False
        else:
            # 流式关闭（测试用）
            result = chat(
                messages, tools=self.tools_schemas,
                model=self.model, base_url=self.base_url,
                api_key=self.api_key, auth_type=self.auth_type,
                max_tokens=self.max_tokens, timeout=self.timeout,
            )

        # 累计可观测指标
        self._update_metrics(result)

        # 流式时用 buffer 内容兜底：Live 面板已显示了 buffer，
        # 但 chat_stream 返回的 result["content"] 可能为空（边缘情况），
        # 导致 _parse_result 的 done 判断出错
        if buffer and not result.get("content"):
            result["content"] = "".join(buffer)

        # 用基类的共享逻辑解析结果
        return self._parse_result(result)

    def _call_streaming(self, messages: list, buffer: list, publish: bool = True) -> dict:
        """流式调用 + 终端渲染（或静默缓冲）。

        参数：
          messages — 消息列表
          buffer   — 用来累积 content 片段的列表（外部可读）
          publish  — True 正常渲染面板；False 静默缓冲（不建持久面板）

        返回：
          chat_stream() 的完整结果 dict
        """
        theme = self.theme

        # Live 初始显示动画 Spinner，首个 token 到达后切换为 Markdown 面板
        with Live(
            Spinner("dots", Text(" Agent 思考中…", style=theme["dim"])),
            console=self.console,
            refresh_per_second=10,
        ) as live:

            def _on_token(piece: str):
                """每收到一个 content 片段就更新面板（或只缓冲）。"""
                buffer.append(piece)
                if publish:
                    live.update(Panel(
                        Markdown("".join(buffer)),
                        title="[bold]Agent[/bold]",
                        title_align="left",
                        border_style=theme["ai"],
                        padding=(0, 1),
                    ))

            result = chat_stream(
                messages, tools=self.tools_schemas,
                model=self.model, base_url=self.base_url,
                api_key=self.api_key, auth_type=self.auth_type,
                max_tokens=self.max_tokens, timeout=self.timeout,
                on_token=_on_token,
            )

            # 工具调用轮没有 content，清掉"思考中"占位
            if not buffer:
                live.update(Text(""))

        return result

    def _update_metrics(self, result: dict):
        """从返回结果里提取可观测指标。"""
        usage = result.get("usage") or {}
        self.last_prompt_tokens = usage.get("prompt_tokens") or 0
        self.total_prompt_tokens += usage.get("prompt_tokens") or 0
        self.total_completion_tokens += usage.get("completion_tokens") or 0
        if result.get("ttft_ms") is not None:
            self.last_ttft_ms = result["ttft_ms"]
