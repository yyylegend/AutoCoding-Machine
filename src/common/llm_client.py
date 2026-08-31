"""OpenAI-compatible LLM 客户端。

提供普通与流式 chat-completions 调用，供 Coding Agent 的 adapter 使用。

【用法】
    from src.common.llm_client import chat

    # 最简单：直接传一句话
    reply = chat("你好")

    # 复杂场景：传完整消息列表
    reply = chat([
        {"role": "system", "content": "你是一个助手"},
        {"role": "user", "content": "你好"},
    ])

【可替换性】
    只要 API 兼容 OpenAI 格式，改 .env 里的三行就能换模型：
    LLM_BASE_URL、LLM_MODEL、LLM_API_KEY。
"""

import requests
from src.config.settings import settings


def chat(messages, tools=None, tool_choice="auto", timeout=None,
         model=None, base_url=None, api_key=None, auth_type=None, max_tokens=None):
    """调用 LLM（兼容 OpenAI 格式）。

    根据是否传 tools,返回类型不同:
      - tools=None → 返回 str(老接口,向后兼容)
      - tools=[...] → 返回 dict {"content", "tool_calls"}

    参数
    ----------
    messages : str 或 list[dict]
        要问的话。
        - 传字符串:chat("你好"),函数自动包装成 LLM 格式
        - 传列表:chat([{"role": "user", "content": "你好"}])
    tools : list 或 None
        OpenAI Tool Calling 的工具定义列表。
        - 不传(或 None):纯文本对话,返回 str
        - 传了:LLM 按 schema 输出工具调用,返回 dict
    tool_choice : str
        "auto" 让 LLM 自己决定调不调;"required" 强制 LLM 调一个工具。
    timeout : int
        最大等待秒数,默认从 settings.LLM_TIMEOUT_SEC 读(默认 120 秒)。

    以下参数用于覆盖 settings 里的默认 LLM 配置。
    不传就用 settings.LLM_* 的值。
    Coding Agent 传这些来用自己单独的模型：

        model     — 模型名，默认 settings.LLM_MODEL
        base_url  — API 地址，默认 settings.LLM_BASE_URL
        api_key   — API 密钥，默认 settings.LLM_API_KEY
        auth_type — 认证方式，默认 settings.LLM_AUTH_TYPE
        max_tokens— 单次生成上限，默认 settings.LLM_MAX_TOKENS

    例如：
        chat(messages, model="gpt-4", base_url="https://xxx/v1")

    返回
    -------
    str 或 dict
        - tools=None:返回 LLM 的文字回复
        - tools=[...]:返回 {"content": str, "tool_calls": list}
          其中 tool_calls 每项形如:
            {"id": "...", "type": "function", "function": {"name": "...", "arguments": "{...}"}}
    """
    # 如果传的是字符串，自动包成 LLM API 要求的格式
    # OpenAI 兼容的接口都认这个结构：
    #   [{"role": "user", "content": "你好"}]
    # 这么写的话用户传字符串也能用，不用每次手动拼列表
    if isinstance(messages, str): messages = [{"role": "user", "content": messages}]

    # 不传 timeout 就使用统一 LLM 超时。
    if timeout is None:
        timeout = settings.LLM_TIMEOUT_SEC

    # 拼请求体：告诉 LLM 用哪个模型、问什么话
    # model       — .env 里配的模型名
    # messages    — 对话消息列表
    # max_tokens  — 防止 GUI-Owl 等在长 system+tools 下无限生成卡死客户端
    #
    # 未显式覆盖时使用统一的 settings.LLM_* 配置。
    # Coding Agent 传自己的 CODING_LLM_* 来覆盖。
    payload = {
        "model": model or settings.LLM_MODEL,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens or settings.LLM_MAX_TOKENS,
    }

    # 把"工具菜单"和"点菜策略"塞进请求体
    # - tools:LLM 可以调的工具列表(像菜单)
    # - tool_choice:"auto" 让 LLM 自己决定调不调,"required" 强制它必须调一个
    # 老调用方不传 tools 时,这段被跳过,行为跟以前完全一样
    if tools is not None:
        payload["tools"] = tools
        # 项目当前只有一个 agent_action 工具。把 required 转成 named choice，
        # 让 vLLM 直接按该函数的 JSON Schema 约束输出；相比 required，
        # GUI-Owl 不会在工具选择/参数生成阶段产生超长或畸形 JSON。
        function_name = tools[0].get("function", {}).get("name") if len(tools) == 1 else None
        if tool_choice == "required" and function_name:
            payload["tool_choice"] = {
                "type": "function",
                "function": {"name": function_name},
            }
        else:
            payload["tool_choice"] = tool_choice
        # Runtime 每轮只执行一个动作。GUI 模型若并行生成 click + fill 等多个
        # tool calls，服务端 parser 可能在后续 JSON 上失败并返回 HTTP 400。
        payload["parallel_tool_calls"] = False

    # 发 HTTP POST 请求到 LLM 服务
    # 只要改 LLM_BASE_URL，就能换到任何 OpenAI 兼容的服务
    # 根据认证方式拼请求头
    # bearer  → 标准 OpenAI 格式: Authorization: Bearer xxx
    # api-key → 小米 MiMo 格式: api-key: xxx
    #
    # Coding Agent 可以传 base_url / api_key / auth_type 来覆盖 settings，
    # 未显式覆盖时自动使用 settings.LLM_*。
    _base_url = base_url or settings.LLM_BASE_URL
    _api_key = api_key or settings.LLM_API_KEY
    _auth_type = (auth_type or settings.LLM_AUTH_TYPE).lower()

    if _auth_type == "api-key":
        auth_header = {"api-key": _api_key}
    else:
        auth_header = {"Authorization": f"Bearer {_api_key}"}

    resp = requests.post(
        # API 地址，比如 https://api.deepseek.com/chat/completions
        # 所有 OpenAI 兼容的服务都在末尾拼 /chat/completions
        f"{_base_url}/chat/completions",
        headers={
            **auth_header,
            # 告诉服务器我发的是 JSON 格式
            "Content-Type": "application/json",
        },
        # json=payload 会自动把字典转成 JSON 字符串发出去
        json=payload,
        # timeout=15 的意思是：连不上或没响应，最多等 15 秒
        # 超过 15 秒就抛 requests.exceptions.Timeout
        # 这是防止 LLM 卡住时整个程序也跟着卡死的关键
        timeout=timeout,
    )

    # 非 2xx 时保留 vLLM 的错误正文。否则 raise_for_status() 只显示 HTTPError，
    # 无法区分 tool JSON 解析失败、上下文超限或服务端异常。
    if not resp.ok:
        raise requests.HTTPError(
            f"LLM API {resp.status_code}: {resp.text[:2000]}",
            response=resp,
        )

    # 从 LLM 返回的 JSON 里取数据
    # 返回格式固定(OpenAI 兼容):
    #   {
    #     "choices": [
    #       {
    #         "message": {
    #           "content": "你好..." 或 null,    ← Tool Calling 时可能是 null
    #           "tool_calls": [...]              ← 老调用方没有这字段
    #         }
    #       }
    #     ],
    #     "usage": {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ...}
    #   }
    data = resp.json()
    message = data["choices"][0]["message"]

    # 根据调用方是否传了菜单,返回不同格式:
    # - 不传 menu(老路径):返回字符串,跟以前一模一样,所有老代码不用改
    # - 传了 menu(新路径):返回 dict,里面有两个东西
    #     content:LLM 说的话(可能为空,因为 LLM 全用"点菜"代替了说话)
    #     tool_calls:LLM 点的菜,每项包含函数名 + 参数
    #     usage:token 消耗(OpenAI 兼容服务都返回;LangSmith 上报成本用)
    if tools is None:
        return message.get("content") or ""

    return {
        "content": message.get("content") or "",
        "tool_calls": message.get("tool_calls") or [],
        "usage": data.get("usage") or {},
    }


def fetch_model_context_window(base_url=None, api_key=None, auth_type=None, model=None):
    """从 LLM 供应商 API 查询模型的上下文窗口大小。

    vLLM 的 /models 端点返回 max_model_len；
    OpenAI 不返回（返回 None，调用方用默认值兜底）。

    参数与 chat() 的覆盖参数一致，不传就用 settings.LLM_*。

    返回：
      int（上下文窗口 token 数）或 None（查询失败 / 供应商不返回）
    """
    _base_url = base_url or settings.LLM_BASE_URL
    _api_key = api_key or settings.LLM_API_KEY
    _auth_type = (auth_type or settings.LLM_AUTH_TYPE).lower()

    if _auth_type == "api-key":
        headers = {"api-key": _api_key}
    else:
        headers = {"Authorization": f"Bearer {_api_key}"}

    try:
        resp = requests.get(f"{_base_url}/models", headers=headers, timeout=10)
        if not resp.ok:
            return None
        target = model or settings.LLM_MODEL
        for m in resp.json().get("data", []):
            if m.get("id") == target:
                # vLLM 返回 max_model_len；OpenAI 没有此字段
                return m.get("max_model_len")
    except Exception:
        return None
    return None


def chat_stream(messages, tools=None, tool_choice="auto", timeout=None,
                model=None, base_url=None, api_key=None, auth_type=None,
                max_tokens=None, on_token=None):
    """流式调用 LLM（SSE），边生成边回调，返回含 usage / TTFT 的结果。

    与 chat() 的区别：
      - 请求体加 stream=True + stream_options.include_usage=True
      - 用 requests 的 stream 模式逐行读 SSE（Server-Sent Events）
      - 每收到一个 content 增量就调 on_token(delta)，用于 CLI 实时显示
      - 返回值统一是 dict：
          {"content", "tool_calls", "usage", "ttft_ms"}
        usage   = {"prompt_tokens", "completion_tokens", "total_tokens"}（服务端支持时）
        ttft_ms = 首字延迟毫秒数（第一个 content token 到达的时间）

    【谁会用】
      只有 CLI 的 SimpleLLMAdapter 用（要实时显示 + 观测指标）。
      CLI 使用此函数实现增量渲染。

    参数 on_token：
      可选回调，签名 on_token(delta_text: str)。每到一个 content 增量就调一次。
      传 None 就不回调（只累积，不实时输出）。
    """
    import json
    import time

    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    if timeout is None:
        timeout = settings.LLM_TIMEOUT_SEC

    payload = {
        "model": model or settings.LLM_MODEL,
        "messages": messages,
        "temperature": 0,
        "max_tokens": max_tokens or settings.LLM_MAX_TOKENS,
        "stream": True,
        # 让服务端在最后一个 chunk 带上 usage（vLLM / OpenAI 都支持）
        "stream_options": {"include_usage": True},
    }
    if tools is not None:
        payload["tools"] = tools
        function_name = tools[0].get("function", {}).get("name") if len(tools) == 1 else None
        if tool_choice == "required" and function_name:
            payload["tool_choice"] = {"type": "function", "function": {"name": function_name}}
        else:
            payload["tool_choice"] = tool_choice
        payload["parallel_tool_calls"] = False

    _base_url = base_url or settings.LLM_BASE_URL
    _api_key = api_key or settings.LLM_API_KEY
    _auth_type = (auth_type or settings.LLM_AUTH_TYPE).lower()
    if _auth_type == "api-key":
        auth_header = {"api-key": _api_key}
    else:
        auth_header = {"Authorization": f"Bearer {_api_key}"}

    resp = requests.post(
        f"{_base_url}/chat/completions",
        headers={**auth_header, "Content-Type": "application/json"},
        json=payload,
        timeout=timeout,
        stream=True,  # 关键：不一次性读完，逐块拿
    )
    if not resp.ok:
        raise requests.HTTPError(
            f"LLM API {resp.status_code}: {resp.text[:2000]}", response=resp,
        )

    # vLLM / OpenAI 的 SSE 响应通常不声明 charset，requests 会默认用 ISO-8859-1
    # 解码，导致 UTF-8 中文乱码。这里强制 UTF-8。
    resp.encoding = "utf-8"

    content_parts = []
    tool_calls_acc = {}   # index -> 累积中的 tool_call（name/arguments 分多个 chunk 到达）
    usage = {}
    ttft_ms = None
    start = time.perf_counter()

    # SSE 格式：每行 "data: {...}"，结束行 "data: [DONE]"
    for raw_line in resp.iter_lines(decode_unicode=True):
        if not raw_line or not raw_line.startswith("data:"):
            continue
        data = raw_line[len("data:"):].strip()
        if data == "[DONE]":
            break
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue

        # usage 通常在最后一个 chunk（可能没有 choices）
        if chunk.get("usage"):
            usage = chunk["usage"]

        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}

        # content 增量 → 记录 TTFT + 回调
        piece = delta.get("content")
        if piece:
            if ttft_ms is None:
                ttft_ms = (time.perf_counter() - start) * 1000
            content_parts.append(piece)
            if on_token:
                on_token(piece)

        # tool_calls 增量 → 按 index 累积
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            acc = tool_calls_acc.setdefault(idx, {
                "id": "", "type": "function",
                "function": {"name": "", "arguments": ""},
            })
            if tc.get("id"):
                acc["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                acc["function"]["name"] += fn["name"]
            if fn.get("arguments"):
                acc["function"]["arguments"] += fn["arguments"]

    tool_calls = [tool_calls_acc[i] for i in sorted(tool_calls_acc)]
    return {
        "content": "".join(content_parts),
        "tool_calls": tool_calls,
        "usage": usage,
        "ttft_ms": ttft_ms,
    }
