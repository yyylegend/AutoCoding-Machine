"""list_dir 工具：列出 workspace 内某个目录下的文件和子目录。

【大白话】
  相当于在项目里 ls / dir 一下。
  输出类似：
    [dir]  src
    [file] README.md
"""

from src.engine.contracts import ToolCall, ToolResult
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools.helpers import (
    DEFAULT_MAX_PATHS,
    clip_text,
    execution_result,
    get_str_arg,
    ok_result,
    permission_result,
)


@tool(name="list_dir", permission="auto")
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int,
) -> ToolResult:
    """执行 list_dir。

    参数：
      tool_call.arguments["path"] — 目录路径，默认 "."
    """
    path = get_str_arg(tool_call, "path")
    if path is None:
        path = "."

    full = sandbox.resolve(path)
    if full is None:
        return permission_result(tool_call, "路径越界，禁止列目录: " + path)

    if not full.exists():
        return execution_result(
            tool_call,
            "目录不存在: " + sandbox.relpath(full),
        )

    if not full.is_dir():
        return execution_result(
            tool_call,
            "不是目录: " + sandbox.relpath(full),
        )

    try:
        items = list(full.iterdir())
    except OSError as exc:
        return execution_result(tool_call, "列目录失败: " + str(exc))

    # 目录在前，文件在后；名字按小写排序，方便人看
    def sort_key(item):
        return (not item.is_dir(), item.name.lower())

    items = sorted(items, key=sort_key)

    lines = []
    total = 0
    for item in items:
        if total >= DEFAULT_MAX_PATHS:
            lines.append("... 仅显示前 " + str(DEFAULT_MAX_PATHS) + " 项")
            break
        if item.is_dir():
            kind = "dir"
        else:
            kind = "file"
        rel = sandbox.relpath(item)
        lines.append("[" + kind + "] " + rel)
        total += 1

    if len(lines) == 0:
        raw = "(空目录)"
    else:
        raw = "\n".join(lines)

    content, truncated = clip_text(raw, max_output_chars)
    return ok_result(
        tool_call,
        content,
        {
            "path": sandbox.relpath(full),
            "count": total,
            "truncated": truncated,
        },
    )


def schema() -> dict:
    """OpenAI-compatible 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "列出 workspace 内某个目录下的文件和子目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径，默认 .",
                    }
                },
                "required": [],
            },
        },
    }
