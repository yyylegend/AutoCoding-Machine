"""/skills 命令。"""

from src.profiles.coding.cli_ui import print_skills


def handle_skills(context):
    """打印可用技能清单。"""
    print_skills(context["skills"])
    return True
