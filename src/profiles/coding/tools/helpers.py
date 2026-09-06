"""Coding 工具的小公共函数。

只放几个大家都会用到的简单帮助函数，避免每个工具文件重复写。

【谁会用】
  read_file.py / list_dir.py / glob_tool.py / grep.py
"""

from src.engine.contracts import ToolCall, ToolResult


# 单次输出默认上限，防止把超大文件整份塞进模型上下文
DEFAULT_MAX_OUTPUT_CHARS = 10000
# grep 默认最多返回多少条匹配
DEFAULT_MAX_MATCHES = 50
# glob / list_dir 默认最多返回多少个路径
DEFAULT_MAX_PATHS = 200


def get_str_arg(tool_call: ToolCall, key: str):
    """从 tool_call.arguments 里取字符串参数。

    没有这个 key、值是 None、或全是空格时，返回 None。

    例子：
        call = ToolCall(id="1", name="read_file", arguments={"path": "src/main.py"})
        get_str_arg(call, "path")  # "src/main.py"
        get_str_arg(call, "missing")  # None
    """
    value = tool_call.arguments.get(key)
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    return text


from src.common.text import clip_text


def invalid_result(tool_call: ToolCall, message: str) -> ToolResult:
    """参数错误。

    适用场景：
      - 必填参数没传
      - 参数类型不对
      - 正则表达式写错

    retryable=False，因为参数错误通常需要模型重新写参数。
    """
    return ToolResult(
        tool_call_id=tool_call.id,
        content=message,
        error=True,
        error_type="invalid_args",
        retryable=False,
    )


def permission_result(tool_call: ToolCall, message: str) -> ToolResult:
    """权限错误，例如路径越界。

    retryable=False，因为同一路径再试还是会失败。
    """
    return ToolResult(
        tool_call_id=tool_call.id,
        content=message,
        error=True,
        error_type="permission",
        retryable=False,
    )


def execution_result(tool_call: ToolCall, message: str) -> ToolResult:
    """执行错误，例如文件不存在、读失败。

    retryable=False（目前默认），但具体要不要重试，
    后面 GuardManager 和 Loop 可以根据 error_type 再判断。
    """
    return ToolResult(
        tool_call_id=tool_call.id,
        content=message,
        error=True,
        error_type="execution",
        retryable=False,
    )


def ok_result(tool_call: ToolCall, content: str, metadata=None) -> ToolResult:
    """成功结果。

    metadata 通常包含：
      - path: 文件路径
      - truncated: 是否截断
      - count: 匹配条数
      - chars: 字符数
    """
    if metadata is None:
        metadata = {}
    return ToolResult(
        tool_call_id=tool_call.id,
        content=content,
        error=False,
        metadata=metadata,
    )
