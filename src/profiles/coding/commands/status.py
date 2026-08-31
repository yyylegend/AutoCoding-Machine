"""/status 命令。"""

from src.engine.context_manager import count_tokens


def handle_status(context):
    """打印当前 CLI 状态。

    参数：
      context：CLI 传入的临时状态字典。
    返回：
      True，表示命令已经处理。
    """
    messages = context["messages"]
    token_budget = context["token_budget"]
    ctx_tokens = count_tokens(messages)
    pct = int(ctx_tokens / token_budget * 100) if token_budget > 0 else 0
    console = context["console"]
    theme = context["theme"]
    llm = context["llm"]
    store = context["store"]
    history = context["history"]
    tools = context["tools"]
    plan_mode = context["plan_mode"]

    console.print(f"\n[{theme['dim']}]╭─ 状态 ─────────────────────────────────╮[/{theme['dim']}]")
    console.print(f"[{theme['dim']}]│[/{theme['dim']}] 模式:     {'PLAN MODE' if plan_mode else '普通'}")
    console.print(f"[{theme['dim']}]│[/{theme['dim']}] 模型:     {llm.model}")
    console.print(f"[{theme['dim']}]│[/{theme['dim']}] 会话:     {store.session_id}")
    console.print(f"[{theme['dim']}]│[/{theme['dim']}] 历史:     {len(history)} 条消息")
    console.print(f"[{theme['dim']}]│[/{theme['dim']}] Token:    ↑{llm.total_prompt_tokens} ↓{llm.total_completion_tokens}")
    console.print(f"[{theme['dim']}]│[/{theme['dim']}] 上下文:   {ctx_tokens}/{token_budget} ({pct}%)")
    console.print(f"[{theme['dim']}]│[/{theme['dim']}] 工具:     {len(tools.get_schemas())} 个已注册")
    console.print(f"[{theme['dim']}]╰──────────────────────────────────────╯[/{theme['dim']}]\n")
    return True
