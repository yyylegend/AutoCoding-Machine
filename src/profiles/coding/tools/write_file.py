"""write_file 工具：创建或覆盖 workspace 内的文件。

【大白话】
  模型说"帮我把这段代码写进 src/utils.py"，
  这个工具就把内容写进去。文件不存在就创建，存在就覆盖。

【安全】
  路径必须先过 WorkspaceSandbox。
  越界直接 permission 错误，不会真去写系统文件。
  父目录不存在时会自动创建（mkdir -p 的效果）。

【权限】
  默认 ASK（需要用户确认），因为写文件是危险操作。
"""

from src.engine.contracts import ToolCall, ToolResult
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools.helpers import (
    execution_result,
    get_str_arg,
    invalid_result,
    ok_result,
    permission_result,
)


@tool(name="write_file", permission="ask")
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int,
) -> ToolResult:
    """执行 write_file。

    参数：
      tool_call.arguments["path"]    — 文件路径（相对 workspace）
      tool_call.arguments["content"] — 要写入的完整文件内容

    成功：
      content = "已写入 xxx（N 字符）"

    失败：
      invalid_args — 没传 path 或 content
      permission   — 路径越界
      execution    — 写入失败（磁盘满、权限不够等）
    """
    # ---- 第 1 步：取参数 ----
    path = get_str_arg(tool_call, "path")
    if path is None:
        return invalid_result(tool_call, "write_file 需要参数 path")

    # content 允许为空字符串（创建空文件），但不能不传
    content = tool_call.arguments.get("content")
    if content is None:
        return invalid_result(tool_call, "write_file 需要参数 content")
    content = str(content)

    # ---- 第 2 步：路径安全检查 ----
    full = sandbox.resolve(path)
    if full is None:
        return permission_result(tool_call, "路径越界，禁止写入: " + path)

    # ---- 第 3 步：自动创建父目录 ----
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return execution_result(tool_call, "创建目录失败: " + str(exc))

    # ---- 第 4 步：写入文件 ----
    try:
        full.write_text(content, encoding="utf-8")
    except OSError as exc:
        return execution_result(tool_call, "写入失败: " + str(exc))

    # ---- 第 5 步：返回成功 ----
    rel = sandbox.relpath(full)
    return ok_result(
        tool_call,
        f"已写入 {rel}（{len(content)} 字符）",
        {
            "path": rel,
            "chars": len(content),
        },
    )


def schema() -> dict:
    """OpenAI-compatible 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "创建或覆盖 workspace 内的文件。如果父目录不存在会自动创建。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对项目根目录的文件路径，例如 src/utils.py",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的完整文件内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    }
