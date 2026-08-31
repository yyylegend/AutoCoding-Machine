"""/sessions 命令。"""

from src.profiles.coding.cli_ui import print_sessions


def handle_sessions(context):
    """打印历史会话清单。"""
    print_sessions(context["sessions"], context["current_id"])
    return True
