"""read_file 工具：读取 workspace 内的文本文件。

【大白话】
  模型说“帮我读 src/main.py”，
  这个工具就去读，然后把内容塞进 ToolResult。

【安全】
  路径必须先过 WorkspaceSandbox。
  越界直接 permission 错误，不会真去读系统文件。
"""

from src.engine.contracts import ToolCall, ToolResult
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools.helpers import (
    clip_text,
    execution_result,
    get_str_arg,
    invalid_result,
    ok_result,
    permission_result,
)


@tool(name="read_file", permission="auto")
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int,
) -> ToolResult:
    """执行 read_file。

    参数：
      tool_call.arguments["path"] — 文件路径

    成功：
      content = 文件文本（可能被截断）

    失败：
      invalid_args — 没传 path
      permission   — 路径越界
      execution    — 文件不存在 / 不是文件 / 读失败
    """
    path = get_str_arg(tool_call, "path")
    if path is None:
        return invalid_result(tool_call, "read_file 需要参数 path")

    full = sandbox.resolve(path)
    if full is None:
        return permission_result(tool_call, "路径越界，禁止读取: " + path)

    if not full.exists():
        return execution_result(
            tool_call,
            "文件不存在: " + sandbox.relpath(full),
        )

    if not full.is_file():
        return execution_result(
            tool_call,
            "不是文件: " + sandbox.relpath(full),
        )

    try:
        # 优先按 utf-8 读；失败时用 replace，避免因为编码直接崩
        try:
            text = full.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = full.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return execution_result(tool_call, "读取失败: " + str(exc))

    content, truncated = clip_text(text, max_output_chars)
    return ok_result(
        tool_call,
        content,
        {
            "path": sandbox.relpath(full),
            "truncated": truncated,
            "chars": len(content),
        },
    )


def schema() -> dict:
    """OpenAI-compatible 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取 workspace 内的文本文件内容。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对项目根目录的文件路径，例如 src/main.py",
                    }
                },
                "required": ["path"],
            },
        },
    }
