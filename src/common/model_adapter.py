"""无界面的模型适配：调用模型，并转换成 Engine 使用的响应。"""

import json
from src.common.llm_client import chat, chat_stream
from src.config.settings import settings
from src.engine.contracts import AgentResponse, ToolCall

class ModelAdapter:
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

    def __init__(self, tools_schemas: list, on_token=None, *, model=None):
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
        self.model = model or settings.CODING_LLM_MODEL
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
            result = self._chat_stream(
                messages, tools=self.tools_schemas,
                model=self.model, base_url=self.base_url,
                api_key=self.api_key, auth_type=self.auth_type,
                max_tokens=self.max_tokens, timeout=self.timeout,
                on_token=self.on_token,
            )
        else:
            # 非流式：一次拿完整结果
            result = self._chat(
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

        return AgentResponse(content=content, tool_calls=tool_calls, done=done, usage=result.get("usage") or {})

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


    def _chat(self, *args, **kwargs):
        return chat(*args, **kwargs)

    def _chat_stream(self, *args, **kwargs):
        return chat_stream(*args, **kwargs)
