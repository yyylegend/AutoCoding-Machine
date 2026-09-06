"""/status 命令。"""

from src.engine.context_manager import count_tokens
from rich.table import Table
from rich.text import Text


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

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style=theme["dim"])
    table.add_column()
    for label, value in [
        ("Profile", context.get("profile", "coding")),
        ("模式", "PLAN" if plan_mode else "普通"),
        ("模型", llm.model),
        ("会话", store.session_id),
        ("历史", f"{len(history)} 条消息"),
        ("用量", f"↑{llm.total_prompt_tokens} ↓{llm.total_completion_tokens}"),
        ("上下文", f"约 {ctx_tokens}/{token_budget} ({pct}%)"),
        ("工具", f"{len(tools.get_schemas())} 个已注册"),
    ]:
        table.add_row(label, Text(str(value)))
    console.print(table)
    return True
