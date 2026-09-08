"""请求前的 token 估算，不代替服务端 usage。

已知模型使用 tiktoken 官方映射；未知模型使用通用编码估算。
接口兼容 OpenAI 不代表 tokenizer 相同。工具定义和消息封装也占空间，
但实际聊天模板由服务端决定，因此整个请求的计数始终标为估算。
"""

from __future__ import annotations

import json
from functools import lru_cache

import tiktoken


_DEFAULT_ENCODING = "cl100k_base"
_DEFAULT_MODEL: str | None = None
# 仅保存调用方明确注册的编码；不猜测第三方模型的 tokenizer。
_MODEL_TO_ENCODING: dict[str, str] = {}


def set_encoding(encoding_name: str = "cl100k_base") -> None:
    """设置未知模型使用的回退编码。"""
    global _DEFAULT_ENCODING
    tiktoken.get_encoding(encoding_name)
    _DEFAULT_ENCODING = encoding_name


def init_tokenizer(model_name: str) -> None:
    """兼容旧入口；只设置默认模型，不覆盖官方映射或用户注册值。"""
    global _DEFAULT_MODEL
    _DEFAULT_MODEL = model_name


def set_model_encoding(model_name: str, encoding_name: str) -> None:
    """调用方已确认实际编码时，按完整模型名注册。"""
    tiktoken.get_encoding(encoding_name)
    _MODEL_TO_ENCODING[model_name] = encoding_name


def _get_encoding_for_model(model_name: str | None) -> str:
    model_name = model_name or _DEFAULT_MODEL
    if model_name in _MODEL_TO_ENCODING:
        return _MODEL_TO_ENCODING[model_name]
    try:
        return tiktoken.encoding_name_for_model(model_name or "")
    except KeyError:
        return _DEFAULT_ENCODING


def tokenizer_description(model_name: str) -> str:
    """说明估算依据，让未知模型的通用回退可见。"""
    encoding = _get_encoding_for_model(model_name)
    if model_name in _MODEL_TO_ENCODING:
        return f"{encoding}（手动映射，请求估算）"
    try:
        tiktoken.encoding_name_for_model(model_name)
    except KeyError:
        return f"{encoding}（通用回退，请求估算）"
    return f"{encoding}（模型编码，请求估算）"


@lru_cache(maxsize=8)
def _get_tiktoken(encoding_name: str) -> tiktoken.Encoding:
    return tiktoken.get_encoding(encoding_name)


def estimate_token_length(text: str, model_name: str | None = None) -> int:
    """对文本编码；特殊 token 的字面内容按普通用户文本处理。"""
    enc = _get_tiktoken(_get_encoding_for_model(model_name))
    return len(enc.encode(text, disallowed_special=()))


def count_tokens_in_message(message: dict, model_name: str | None = None) -> int:
    """估算正文、角色、工具调用及关联 ID，另给消息边界留少量空间。"""
    total = 3
    for key in ("role", "content", "name", "tool_calls", "tool_call_id"):
        value = message.get(key)
        if value is None:
            continue
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        total += estimate_token_length(text, model_name)
    return total


def count_tool_tokens(tools: list | None, model_name: str | None = None) -> int:
    """工具 Schema 也会进入模型输入；JSON 长度只是模板开销的近似值。"""
    if not tools:
        return 0
    return estimate_token_length(json.dumps(tools, ensure_ascii=False), model_name)


def get_token_count(messages: list[dict], model_name: str | None = None, *, tools=None) -> int:
    """估算当前消息与工具定义；额外预留回复起始标记。"""
    total = 3 if messages else 0
    for message in messages:
        total += count_tokens_in_message(message, model_name)
    return total + count_tool_tokens(tools, model_name)


def count_tokens_old_style(messages: list) -> int:
    """兼容旧调用，返回估算值。"""
    return get_token_count(messages)
