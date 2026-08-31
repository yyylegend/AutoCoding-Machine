"""/help 命令。"""

from src.profiles.coding.cli_ui import print_help


def handle_help(_context):
    """打印 CLI 帮助。"""
    print_help()
    return True
