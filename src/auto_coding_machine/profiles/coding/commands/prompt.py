"""/prompt 命令。"""

from auto_coding_machine.profiles.coding.cli_ui import print_prompt_debug


def handle_prompt(context):
    """打印当前发给模型的消息结构。"""
    print_prompt_debug(context["messages"])
    return True
