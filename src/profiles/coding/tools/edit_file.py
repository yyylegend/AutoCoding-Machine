"""edit_file 工具：局部替换 workspace 内文件的某段文本。

【大白话】
  模型说"把 src/main.py 里的 old_code 换成 new_code"，
  这个工具就去做搜索替换。

  和 write_file 的区别：
    write_file = 整个文件覆盖（适合新建文件）
    edit_file  = 只改一小段（适合修改已有文件）

【安全】
  路径必须先过 WorkspaceSandbox。
  越界直接 permission 错误。

【设计要点】
  old_text 必须在文件中恰好出现 1 次。
  如果出现 0 次 → 报错"找不到"
  如果出现 2+ 次 → 报错"不唯一，请给更多上下文"
  这样模型就不会误改多处。

【权限】
  默认 ASK（需要用户确认），因为改文件是危险操作。
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


@tool(name="edit_file", permission="ask")
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int,
) -> ToolResult:
    """执行 edit_file。

    参数：
      tool_call.arguments["path"]     — 文件路径（相对 workspace）
      tool_call.arguments["old_text"] — 要被替换的原文（必须唯一出现）
      tool_call.arguments["new_text"] — 替换后的新文本

    成功：
      content = 替换结果摘要（含前后几行上下文）

    失败：
      invalid_args — 缺参数
      permission   — 路径越界
      execution    — 文件不存在 / old_text 找不到 / old_text 不唯一 / 写入失败
    """
    # ---- 第 1 步：取参数 ----
    path = get_str_arg(tool_call, "path")
    if path is None:
        return invalid_result(tool_call, "edit_file 需要参数 path")

    old_text = tool_call.arguments.get("old_text")
    if old_text is None or str(old_text) == "":
        return invalid_result(tool_call, "edit_file 需要参数 old_text（不能为空）")
    old_text = str(old_text)

    new_text = tool_call.arguments.get("new_text")
    if new_text is None:
        return invalid_result(tool_call, "edit_file 需要参数 new_text")
    new_text = str(new_text)

    # ---- 第 2 步：路径安全检查 ----
    full = sandbox.resolve(path)
    if full is None:
        return permission_result(tool_call, "路径越界，禁止编辑: " + path)

    # ---- 第 3 步：读文件 ----
    if not full.exists() or not full.is_file():
        return execution_result(tool_call, "文件不存在: " + sandbox.relpath(full))

    try:
        content = full.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return execution_result(tool_call, "读取失败: " + str(exc))

    # ---- 第 4 步：检查 old_text 出现次数 ----
    count = content.count(old_text)
    if count == 0:
        return execution_result(
            tool_call,
            f"在 {sandbox.relpath(full)} 中找不到要替换的文本。"
            f"请确认 old_text 和文件内容完全一致（包括空格和换行）。",
        )
    if count > 1:
        return execution_result(
            tool_call,
            f"要替换的文本在 {sandbox.relpath(full)} 中出现了 {count} 次，不唯一。"
            f"请提供更多上下文让 old_text 只匹配一处。",
        )

    # ---- 第 5 步：执行替换 ----
    new_content = content.replace(old_text, new_text, 1)

    # ---- 第 6 步：写回文件 ----
    try:
        full.write_text(new_content, encoding="utf-8")
    except OSError as exc:
        return execution_result(tool_call, "写入失败: " + str(exc))

    # ---- 第 7 步：生成 diff 摘要 ----
    summary = _make_summary(content, old_text, new_text)
    rel = sandbox.relpath(full)

    return ok_result(
        tool_call,
        f"已编辑 {rel}\n\n{summary}",
        {
            "path": rel,
            "old_chars": len(old_text),
            "new_chars": len(new_text),
            # 带 old_text/new_text 给前端做并排 diff 视图（spec §6 方案 A）
            # JSONL 不落 metadata，但 WS 事件 tool.result 会带，前端据此渲染 diff
            "old_text": old_text,
            "new_text": new_text,
        },
    )


def _make_summary(original: str, old_text: str, new_text: str) -> str:
    """生成替换前后的简短摘要，方便模型确认改对了。

    只显示替换位置前后各 3 行上下文。
    """
    # 找到 old_text 在原文中的位置
    pos = original.find(old_text)
    if pos < 0:
        return "(无法生成摘要)"

    # 取替换位置前面的几行
    before = original[:pos]
    before_lines = before.splitlines()
    context_before = before_lines[-3:] if len(before_lines) >= 3 else before_lines

    # 取替换位置后面的几行
    after_start = pos + len(old_text)
    after = original[after_start:]
    after_lines = after.splitlines()
    context_after = after_lines[:3] if len(after_lines) >= 3 else after_lines

    # 拼摘要
    parts = []
    if context_before:
        parts.append("  " + "\n  ".join(context_before))
    parts.append("- " + old_text.replace("\n", "\n- "))
    parts.append("+ " + new_text.replace("\n", "\n+ "))
    if context_after:
        parts.append("  " + "\n  ".join(context_after))

    return "\n".join(parts)


def schema() -> dict:
    """OpenAI-compatible 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": (
                "局部替换文件中的一段文本。"
                "old_text 必须在文件中恰好出现 1 次，否则报错。"
                "适合对已有文件做小范围修改。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对项目根目录的文件路径，例如 src/main.py",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "要被替换的原文（必须和文件内容完全一致，包括空格换行）",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "替换后的新文本",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    }
