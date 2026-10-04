"""/cost 命令。"""


def handle_cost(context):
    """指标在每次用户任务前清零；缺少 usage 时只显示已报告部分。"""
    console = context["console"]
    theme = context["theme"]
    llm = context["llm"]
    total = llm.total_prompt_tokens + llm.total_completion_tokens
    console.print(
        f"\n[{theme['dim']}]  本次任务已报告："
        f"↑{llm.total_prompt_tokens} ↓{llm.total_completion_tokens} token"
        f"（共 {total}）[/{theme['dim']}]\n"
    )
    if not getattr(llm, "usage_complete", True):
        console.print("服务端未完整返回用量，以上仅为已报告部分，总消耗未知。")
    return True
