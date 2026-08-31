"""Coding Agent 的保护路径策略。"""

from pathlib import Path


_WRITE_TOOLS = {"write_file", "edit_file"}
_PROTECTED_NAMES = {".env", ".git", ".autocoding"}


def create_protected_path_check(workspace):
    """创建一个检查器，禁止写入项目敏感路径。"""
    root = Path(workspace).resolve()

    def check_path(tool_name, arguments, **_kwargs):
        if tool_name not in _WRITE_TOOLS:
            return "allow"
        path_value = (arguments or {}).get("path")
        if not path_value:
            return "allow"
        target = (root / str(path_value)).resolve()
        try:
            relative = target.relative_to(root)
        except ValueError:
            # 工作区外路径交给原有工具校验，这里不扩大策略范围。
            return "allow"
        if any(part in _PROTECTED_NAMES for part in relative.parts):
            return "deny"
        return "allow"

    return check_path
