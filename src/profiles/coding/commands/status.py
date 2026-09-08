"""/status 命令。"""

from src.common.token_utils import get_token_count, tokenizer_description
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
    console = context["console"]
    theme = context["theme"]
    llm = context["llm"]
    store = context["store"]
    history = context["history"]
    tools = context["tools"]
    plan_mode = context["plan_mode"]
    ctx_tokens = get_token_count(messages, llm.model, tools=tools.get_schemas())
    pct = int(ctx_tokens / token_budget * 100) if token_budget > 0 else 0
    info = context.get("context_info", {"window": None, "source": "未知"})
    window = f"{info['window']:,}（{info['source']}）" if info["window"] else "未知（未获取到服务端窗口）"
    usage_note = "" if getattr(llm, "usage_complete", True) else "（报告不完整，总量未知）"
    last_prompt = getattr(llm, "last_prompt_tokens", None)

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style=theme["dim"])
    table.add_column()
    for label, value in [
        ("Profile", context.get("profile", "coding")),
        ("模式", "PLAN" if plan_mode else "普通"),
        ("模型", llm.model),
        ("会话", store.session_id),
        ("历史", f"{len(history)} 条消息"),
        ("本次任务已报告", f"↑{llm.total_prompt_tokens} ↓{llm.total_completion_tokens}{usage_note}"),
        ("上次请求输入", last_prompt if last_prompt is not None else "未知（尚无报告）"),
        ("模型窗口", window),
        ("输入预算", f"{token_budget:,}（{context.get('budget_source', '调用方设置')}）"),
        ("当前输入估算", f"约 {ctx_tokens}/{token_budget} ({pct}%，含工具定义)"),
        ("计数依据", tokenizer_description(llm.model)),
        ("工具", f"{len(tools.get_schemas())} 个已注册"),
    ]:
        table.add_row(label, Text(str(value)))
    console.print(table)
    return True
