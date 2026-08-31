"""glob_tool：按通配符找文件。

【大白话】
  例如 pattern="**/*.py"
  就会把项目里的 Python 文件列出来。

【注意】
  只输出 workspace 内的路径。
  结果太多时截断，避免把上下文塞爆。
"""

from src.engine.contracts import ToolCall, ToolResult
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools.helpers import (
    DEFAULT_MAX_PATHS,
    clip_text,
    execution_result,
    get_str_arg,
    invalid_result,
    ok_result,
    permission_result,
)


@tool(name="glob", permission="auto")
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int,
) -> ToolResult:
    """执行 glob。

    参数：
      pattern — 必填，例如 **/*.py
      path    — 搜索起点，默认 .
    """
    pattern = get_str_arg(tool_call, "pattern")
    if pattern is None:
        return invalid_result(tool_call, "glob 需要参数 pattern")

    root_text = get_str_arg(tool_call, "path")
    if root_text is None:
        root_text = "."

    root = sandbox.resolve(root_text)
    if root is None:
        return permission_result(tool_call, "路径越界，禁止搜索: " + root_text)

    if not root.exists():
        return execution_result(
            tool_call,
            "起点不存在: " + sandbox.relpath(root),
        )

    if not root.is_dir():
        return execution_result(
            tool_call,
            "起点不是目录: " + sandbox.relpath(root),
        )

    try:
        # 简单规则：
        # - pattern 里有 **，按递归找
        # - 否则只在当前起点做普通 glob
        if "**" in pattern:
            clean = pattern
            # rglob 不太喜欢开头的 **/
            while clean.startswith("**/"):
                clean = clean[3:]
            matches = list(root.rglob(clean))
        else:
            matches = list(root.glob(pattern))
    except (OSError, ValueError) as exc:
        return execution_result(tool_call, "glob 失败: " + str(exc))

    matches = sorted(matches)

    lines = []
    total = 0
    for item in matches:
        # 再保险一次：只输出 workspace 内路径
        try:
            item.resolve().relative_to(sandbox.workspace)
        except Exception:
            continue

        if total >= DEFAULT_MAX_PATHS:
            lines.append("... 仅显示前 " + str(DEFAULT_MAX_PATHS) + " 个匹配")
            break

        if item.is_dir():
            kind = "dir"
        else:
            kind = "file"
        lines.append("[" + kind + "] " + sandbox.relpath(item))
        total += 1

    if len(lines) == 0:
        raw = "(无匹配)"
    else:
        raw = "\n".join(lines)

    content, truncated = clip_text(raw, max_output_chars)
    return ok_result(
        tool_call,
        content,
        {
            "pattern": pattern,
            "path": sandbox.relpath(root),
            "count": total,
            "truncated": truncated,
        },
    )


def schema() -> dict:
    """OpenAI-compatible 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": "glob",
            "description": "按通配符查找文件，例如 **/*.py。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "glob 模式",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索起点目录，默认 .",
                    },
                },
                "required": ["pattern"],
            },
        },
    }
