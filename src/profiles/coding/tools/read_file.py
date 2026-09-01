"""read_file 工具：读取 workspace 内的文本文件，支持按行分页。

【大白话】
  模型说“帮我读 src/main.py”，
  这个工具就去读，然后把内容塞进 ToolResult。

  文件很长时一次读不完（会被截断），模型以前只能自己写临时脚本来分段读。
  现在直接支持 start_line / end_line 参数，按行返回一段内容并带上真实行号，
  临时脚本就没有存在的必要了。

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

# 分页读取时一次建议读多少行（续读提示里用，模型可以自由调整）
PAGE_STEP = 200


def _parse_line(value):
    """把模型传来的行号参数解析成正整数。

    模型传来的值可能是 int，也可能是数字字符串（JSON 里手滑写成 "200"）。
    两种都接受；其他一律返回 None（表示非法）。

    为什么不接受 0 或负数：行号从 1 开始（1-based），0 没有意义。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        return None
    return number if number >= 1 else None


@tool(name="read_file", permission="auto")
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int,
) -> ToolResult:
    """执行 read_file。

    参数：
      tool_call.arguments["path"]       — 文件路径
      tool_call.arguments["start_line"] — 可选，起始行号（1-based，包含）
      tool_call.arguments["end_line"]   — 可选，结束行号（1-based，包含）

    成功：
      content = 文件文本（可能被截断）；
      分页时每行带真实行号，未读完时附下一段建议范围。

    失败：
      invalid_args — 没传 path，或行号参数非法
      permission   — 路径越界
      execution    — 文件不存在 / 不是文件 / 读失败
    """
    path = get_str_arg(tool_call, "path")
    if path is None:
        return invalid_result(tool_call, "read_file 需要参数 path")

    # ---- 分页参数：只在传了的情况下校验，没传保持旧行为 ----
    raw_start = tool_call.arguments.get("start_line")
    raw_end = tool_call.arguments.get("end_line")
    start_line = None if raw_start is None else _parse_line(raw_start)
    end_line = None if raw_end is None else _parse_line(raw_end)

    if raw_start is not None and start_line is None:
        return invalid_result(tool_call, "start_line 必须是 >= 1 的整数")
    if raw_end is not None and end_line is None:
        return invalid_result(tool_call, "end_line 必须是 >= 1 的整数")
    if start_line is not None and end_line is not None and end_line < start_line:
        return invalid_result(
            tool_call,
            "end_line（%d）不能小于 start_line（%d）" % (end_line, start_line),
        )

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

    metadata = {
        "path": sandbox.relpath(full),
        "chars": 0,  # 下面算完再填
        "truncated": False,
    }

    # ---- 无分页参数：保持旧行为，原样返回 ----
    if start_line is None and end_line is None:
        content, truncated = clip_text(text, max_output_chars)
        metadata["truncated"] = truncated
        metadata["chars"] = len(content)
        return ok_result(tool_call, content, metadata)

    # ---- 分页读取：按行切出请求的范围 ----
    lines = text.splitlines()
    total = len(lines)
    metadata["total_lines"] = total
    # 只传 end_line 时表示从第 1 行开始。
    if start_line is None:
        start_line = 1

    # 起始行超过文件末尾：不是参数错，是范围错，如实告知
    if start_line > total:
        return execution_result(
            tool_call,
            "start_line（%d）超出文件行数（共 %d 行）" % (start_line, total),
        )

    # end_line 不传 = 读到文件末尾
    end_index = end_line if end_line is not None else total
    # 超出末尾的部分直接裁掉（end_line 比 total 大时）
    end_index = min(end_index, total)
    selected = lines[start_line - 1:end_index]

    # 按完整行从前往后填充，保证 end_line 与实际返回的末行一致，
    # 避免字符截断后的续读建议跳过未返回的中间行。
    rendered = []
    rendered_chars = 0
    for offset, line in enumerate(selected):
        formatted = "%d| %s" % (start_line + offset, line)
        extra = len(formatted) + (1 if rendered else 0)
        if max_output_chars > 0 and rendered and rendered_chars + extra > max_output_chars:
            break
        if max_output_chars > 0 and not rendered and extra > max_output_chars:
            formatted = formatted[:max_output_chars]
        rendered.append(formatted)
        rendered_chars += len(formatted) + (1 if len(rendered) > 1 else 0)
        if max_output_chars > 0 and rendered_chars >= max_output_chars:
            break

    content = "\n".join(rendered)
    returned_count = len(rendered)
    truncated = returned_count < len(selected)
    metadata["truncated"] = truncated
    metadata["chars"] = len(content)
    metadata["start_line"] = start_line
    metadata["end_line"] = start_line - 1 + returned_count

    # 没读到文件末尾时，给模型一个明确的续读建议
    last_read = metadata["end_line"]
    if last_read < total:
        next_start = last_read + 1
        next_end = min(last_read + PAGE_STEP, total)
        content += (
            "\n...[第 %d - %d 行未显示，共 %d 行；"
            "请用 start_line=%d, end_line=%d 继续读取]" % (
                last_read + 1, total, total, next_start, next_end,
            )
        )

    return ok_result(tool_call, content, metadata)


def schema() -> dict:
    """OpenAI-compatible 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "读取 workspace 内的文本文件内容。"
                "长文件建议用 start_line / end_line 分段读取。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "相对项目根目录的文件路径，例如 src/main.py",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "可选。起始行号，从 1 开始（包含）。"
                                       "长文件请分段读取，不要为读文件创建临时脚本。",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "可选。结束行号（包含）。不传则读到文件末尾。",
                    },
                },
                "required": ["path"],
            },
        },
    }
