"""grep 工具：在文本文件里搜索关键字。

【大白话】
  相当于在项目里搜代码。
  输出类似：
    src/main.py:12: def login():

【默认行为】
  - 默认是普通字符串包含匹配，不是正则
  - 传 regex=true 才按正则
  - 会跳过 .git / .venv / 图片 / 模型权重 等噪音
"""

import re

from src.engine.contracts import ToolCall, ToolResult
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools.helpers import (
    DEFAULT_MAX_MATCHES,
    clip_text,
    execution_result,
    get_str_arg,
    invalid_result,
    ok_result,
    permission_result,
)


@tool(name="grep", permission="auto")
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int,
) -> ToolResult:
    """执行 grep。

    参数：
      query       — 必填，搜索关键字
      path        — 搜索起点，默认 .
      regex       — 是否正则，默认 false
      max_matches — 最多返回多少条，默认 50
    """
    query = get_str_arg(tool_call, "query")
    if query is None:
        return invalid_result(tool_call, "grep 需要参数 query")

    root_text = get_str_arg(tool_call, "path")
    if root_text is None:
        root_text = "."

    use_regex = bool(tool_call.arguments.get("regex", False))

    max_matches = tool_call.arguments.get("max_matches", DEFAULT_MAX_MATCHES)
    try:
        max_matches = int(max_matches)
    except Exception:
        max_matches = DEFAULT_MAX_MATCHES
    if max_matches <= 0:
        max_matches = DEFAULT_MAX_MATCHES

    root = sandbox.resolve(root_text)
    if root is None:
        return permission_result(tool_call, "路径越界，禁止搜索: " + root_text)

    if not root.exists():
        return execution_result(
            tool_call,
            "路径不存在: " + sandbox.relpath(root),
        )

    pattern = None
    if use_regex:
        try:
            pattern = re.compile(query)
        except re.error as exc:
            return invalid_result(tool_call, "正则无效: " + str(exc))

    # 收集要扫描的文件
    files = []
    if root.is_file():
        files.append(root)
    else:
        for item in root.rglob("*"):
            if not item.is_file():
                continue
            if should_skip(item):
                continue
            files.append(item)

    lines = []
    total = 0
    for file_path in files:
        if total >= max_matches:
            break

        try:
            text = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        line_no = 0
        for line in text.splitlines():
            line_no += 1
            hit = False
            if pattern is not None:
                if pattern.search(line) is not None:
                    hit = True
            else:
                if query in line:
                    hit = True

            if not hit:
                continue

            rel = sandbox.relpath(file_path)
            # 去掉行尾空白，输出干净一点
            lines.append(rel + ":" + str(line_no) + ": " + line.rstrip())
            total += 1
            if total >= max_matches:
                break

    if total >= max_matches:
        lines.append("... 仅显示前 " + str(max_matches) + " 条匹配")

    if len(lines) == 0:
        raw = "(无匹配)"
    else:
        raw = "\n".join(lines)

    content, truncated = clip_text(raw, max_output_chars)
    return ok_result(
        tool_call,
        content,
        {
            "query": query,
            "path": sandbox.relpath(root),
            "count": total,
            "regex": use_regex,
            "truncated": truncated,
        },
    )


def should_skip(path) -> bool:
    """跳过明显无关或危险的大目录、二进制文件。"""
    parts = set(path.parts)
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        "models",
        "screenshots",
        "logs",
        ".pytest_cache",
    }
    if len(parts & skip_dirs) > 0:
        return True

    binary_suffix = {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
        ".pdf", ".zip", ".7z", ".rar", ".tar", ".gz",
        ".exe", ".dll", ".so", ".dylib",
        ".pt", ".bin", ".onnx", ".safetensors",
        ".db", ".sqlite", ".sqlite3",
    }
    if path.suffix.lower() in binary_suffix:
        return True
    return False


def schema() -> dict:
    """OpenAI-compatible 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": "grep",
            "description": "在文本文件中搜索关键字，返回 path:line:content。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "要搜索的关键字",
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索起点，默认 .",
                    },
                    "regex": {
                        "type": "boolean",
                        "description": "是否按正则匹配，默认 false",
                    },
                    "max_matches": {
                        "type": "integer",
                        "description": "最多返回多少条匹配，默认 50",
                    },
                },
                "required": ["query"],
            },
        },
    }
