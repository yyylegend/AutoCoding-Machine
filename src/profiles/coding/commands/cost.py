"""/cost 命令。"""


def handle_cost(context):
    """打印当前会话的 Token 消耗。"""
    console = context["console"]
    theme = context["theme"]
    llm = context["llm"]
    total = llm.total_prompt_tokens + llm.total_completion_tokens
    console.print(
        f"\n[{theme['dim']}]  本次会话："
        f"↑{llm.total_prompt_tokens} ↓{llm.total_completion_tokens} token"
        f"（共 {total}）[/{theme['dim']}]\n"
    )
    return True
