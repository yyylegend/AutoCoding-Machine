"""/memory 命令。"""

from src.config.settings import settings
from src.profiles.coding.cli_ui import console
from src.profiles.coding.cli_ui import THEME
from src.profiles.coding.tools.memory_tool import _user_memory_path


def handle_memory(context):
    """显示项目和用户长期记忆文件。"""
    workspace = context["workspace"]
    project_mem = workspace / ".autocoding" / "MEMORY.md"
    user_mem = _user_memory_path()
    for label, path in [("MEMORY.md（项目）", project_mem), ("USER.md（用户）", user_mem)]:
        if path.is_file():
            content = path.read_text(encoding="utf-8").strip()
            console.print(
                f"\n[{THEME['accent']}]── {label} ({len(content)}/{settings.MEMORY_CHAR_LIMIT} 字符) ──[/{THEME['accent']}]"
            )
            console.print(content if content else f"[{THEME['dim']}](空)[/{THEME['dim']}]")
        else:
            console.print(f"\n[{THEME['dim']}]── {label}: 文件不存在 ──[/{THEME['dim']}]")
    console.print()
    return True
