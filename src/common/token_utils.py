"""Token 计算工具。

【作用】
  为上下文管理器、CLI 状态栏等提供精确的 token 计数能力。

【实现选择】
  - 使用 tiktoken（OpenAI 官方 tokenizer）
  - 适用于 GLM-Edge / Minimax / DeepSeek / MiMo 等 OpenAI 兼容 API
  - 速度快、准确度高，无需下载额外模型文件

【为什么用 tiktoken】
  - 这些 API 的 tokenization 与 GPT-3.5/GPT-4 类似
  - tiktoken 是 OpenAI 官方推荐，精度接近真实值
  - 轻量级：纯 Python + C extension，无需下载 GB 级模型

【用法】
    from src.common.token_utils import get_token_count

    # 直接调用（默认 encoding_name="cl100k_base"）
    tokens = get_token_count(messages)

    # 指定 model_name（某些特殊编码）
    tokens = get_token_count(messages, model_name="gpt-4o")
"""

from __future__ import annotations

import tiktoken


# ============================================================
# 全局配置
# ============================================================

# 默认编码方案（GPT-3.5/GPT-4/大多数 OpenAI 兼容 API 通用）
_DEFAULT_ENCODING: str = "cl100k_base"

# 支持更多 model_name -> encoding_name 映射
_MODEL_TO_ENCODING: dict[str, str] = {
    # OpenAI 系列（当前主流）
    "gpt-3.5-turbo": "cl100k_base",
    "gpt-3.5": "cl100k_base",
    "gpt-4": "cl100k_base",
    "gpt-4o": "cl100k_base",
    "gpt-4o-mini": "cl100k_base",
    "gpt-4-turbo": "cl100k_base",

    # DeepSeek（Coding Agent 实际在用）
    "deepseek-chat": "cl100k_base",
    "deepseek-coder": "r50k_base",  # 代码专用编码器
}


def set_encoding(encoding_name: str = _DEFAULT_ENCODING) -> None:
    """设置默认的 encoding_name。

    参数：
      encoding_name — tiktoken 支持的编码名称，如：
        - "cl100k_base" (GPT-3.5/4)
        - "p50k_base" (davinci)
        - "r50k_base" (codex)
    """
    global _DEFAULT_ENCODING
    _DEFAULT_ENCODING = encoding_name


def init_tokenizer(model_name: str) -> None:
    """初始化默认 tokenizer（快速启动）。

    参数：
      model_name — 模型名称字符串

    说明：
      这个函数只是为了兼容旧代码名。
      实际 tiktoken 是延迟加载的，所以这个函数现在只做一次注册。
    """
    set_model_encoding(model_name, _DEFAULT_ENCODING)


def set_model_encoding(model_name: str, encoding_name: str) -> None:
    """注册 model_name -> encoding_name 映射。

    参数：
      model_name — 你的模型名（例如从 .env 来的）
      encoding_name — 对应的 tiktoken 编码名

    示例：
        # 如果你的环境用 Minimax 模型，它用的是 p50k_base
        set_model_encoding("minimax-v1", "p50k_base")
    """
    _MODEL_TO_ENCODING[model_name] = encoding_name


# ============================================================
# 公共 API
# ============================================================

def count_tokens_in_message(message: dict, model_name: str | None = None) -> int:
    """计算单条消息的 token 数。

    参数：
      message — 消息字典，包含 role/content/tool_calls 等字段
      model_name — 可选的模型名。如果已注册到 MODEL_TO_ENCODING，自动选 encoding

    返回：
      token 数量（整数）

    说明：
      - 提取 content 和 tool_calls 字段
      - 用 tiktoken 编码后计算长度
    """
    # 确定使用哪个 encoding
    encoding_name = _get_encoding_for_model(model_name)
    enc = _get_tiktoken(encoding_name)

    # 提取文本内容
    content = message.get("content", "") or ""
    text = str(content)

    # 如果有 tool_calls，将其转换为字符串加入
    tool_calls = message.get("tool_calls")
    if tool_calls:
        # 简化处理：把 tool_calls 转成 JSON 字符串再加入
        import json
        try:
            text += "\n\n" + json.dumps(tool_calls, ensure_ascii=False)
        except Exception:
            text += "\n\n" + str(tool_calls)

    # 编码并计数
    encoded = enc.encode(text)
    return len(encoded)


def get_token_count(messages: list[dict], model_name: str | None = None) -> int:
    """获取消息列表的总 token 数。

    参数：
      messages — 消息列表（list of dict）
      model_name — 可选的模型名（用于查找 encoding）

    返回：
      token 总数

    用法：
        from src.common.token_utils import get_token_count

        # 方式 1：默认 cl100k_base（适合 GPT-3.5/4/GLM/DeepSeek）
        tokens = get_token_count(messages)

        # 方式 2：指定 model_name（自动查找 encoding）
        tokens = get_token_count(messages, model_name="deepseek-chat")

        # 方式 3：全局设置 encoding
        set_model_encoding("my-minimax-model", "p50k_base")
        tokens = get_token_count(messages, model_name="my-minimax-model")
    """
    total = 0
    for msg in messages:
        total += count_tokens_in_message(msg, model_name)
    return total


def estimate_token_length(text: str, model_name: str | None = None) -> int:
    """估算一段纯文本的 token 数（快速预览用）。

    参数：
      text — 文本字符串
      model_name — 可选的模型名

    返回：
      预估 token 数

    用法：
        length = estimate_token_length("你好，世界！")
        # 约等于 4 tokens
    """
    encoding_name = _get_encoding_for_model(model_name)
    enc = _get_tiktoken(encoding_name)

    return len(enc.encode(text))


# ============================================================
# 内部辅助函数
# ============================================================

def _get_encoding_for_model(model_name: str | None) -> str:
    """根据 model_name 查找对应的 encoding_name。

    优先级：
      1. 已注册的 mapping (_MODEL_TO_ENCODING)
      2. 默认值 (_DEFAULT_ENCODING)
    """
    if model_name is None:
        return _DEFAULT_ENCODING

    # 先查 exact match
    if model_name in _MODEL_TO_ENCODING:
        return _MODEL_TO_ENCODING[model_name]

    # 再查模糊匹配（以...开头）
    for prefix, encoding in _MODEL_TO_ENCODING.items():
        if model_name.startswith(prefix):
            return encoding

    # 都没找到，返回默认
    return _DEFAULT_ENCODING


# 缓存 tiktoken.Encoder 实例
_encoder_cache: dict[str, tiktoken.Encoding] = {}


def _get_tiktoken(encoding_name: str) -> tiktoken.Encoding:
    """获取或创建 tiktoken Encoder 实例（缓存）。

    参数：
      encoding_name — 编码名称

    返回：
      tiktoken.Encoding 实例
    """
    if encoding_name not in _encoder_cache:
        try:
            _encoder_cache[encoding_name] = tiktoken.get_encoding(encoding_name)
        except Exception as e:
            # 万一失败，回退到默认的 cl100k_base
            import warnings
            warnings.warn(f"Failed to load {encoding_name}: {e}. Using default.")
            _encoder_cache[encoding_name] = tiktoken.get_encoding(_DEFAULT_ENCODING)

    return _encoder_cache[encoding_name]


# ============================================================
# 兼容性导出（为了适配现有代码）
# ============================================================

def count_tokens_old_style(messages: list) -> int:
    """兼容旧版 context_manager.count_tokens() 的签名。

    【注意】这个函数会逐步被 replace，但为了现有代码不崩，先保留。

    参数：
      messages — 消息列表

    返回：
      token 数（精确值，不再是字符估算）
    """
    return get_token_count(messages)
