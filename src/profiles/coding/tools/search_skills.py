"""search_skills 工具：按关键词搜索可用技能（渐进式披露的 Discovery 层）。

【大白话】
  启动时模型只看到一堆技能"名字"（很省 token）。
  当模型觉得"这个任务可能需要某个技能"时，就调这个工具搜一下：
    - 输入关键词，返回匹配的技能
    - 可以只看名字（detail="name"，最省），或看名字+描述（detail="full"）
  确认要用了，再调 load_skill 加载完整说明。

  关键原理：筛选发生在 Python 代码里（不花 token），
  模型从头到尾只看到"搜出来的那几个"，看不到全部技能。
  这就是 Anthropic 推荐的 search_tools 模式。

【为什么 permission="auto"】
  只是在内存里的技能清单中做字符串匹配，纯只读、无副作用，
  不读文件、不执行命令，所以直接放行，不用问用户。

【谁会用】
  MachineLoop → ToolManager → execute() 调用此工具的 execute 函数
"""

from src.engine.contracts import ToolCall, ToolResult
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.skills import discover_skills
from src.profiles.coding.tools.helpers import (
    get_str_arg,
    ok_result,
)


# full 模式（名字+描述）最多返回多少个，防止描述太长撑爆上下文。
# name 模式不限制：名字很小（每个约几个 token），
# LLM 需要看到全部技能名字才能发现可用技能（渐进式披露的 L1 层应该完整）。
MAX_RESULTS_FULL = 20


@tool(name="search_skills", permission="auto")
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int,
) -> ToolResult:
    """执行 search_skills：按关键词搜索技能。

    参数：
      tool_call.arguments["query"]  — 搜索关键词（可不传，不传就列出全部名字）
      tool_call.arguments["detail"] — 详细程度："name"（默认，只回名字）
                                      或 "full"（回名字+描述）

    返回：
      content = 匹配到的技能列表（按 detail 决定详细程度）
    """
    # ---- 第一步：读取参数 ----
    # query 可以为空（空就列出全部）；detail 默认 "name"
    query = get_str_arg(tool_call, "query")
    detail = get_str_arg(tool_call, "detail")
    if detail is None:
        detail = "name"

    # ---- 第二步：拿到全部技能清单 ----
    # sandbox.workspace 是项目根目录，用来定位项目级技能目录
    skills = discover_skills(sandbox.workspace)

    # ---- 第三步：按关键词过滤（在代码里做，不花 token）----
    # query 为空 → 不过滤，返回全部
    # query 非空 → 在 name 或 description 里找（不区分大小写）
    if query is None:
        matched = skills
    else:
        q = query.lower()
        matched = []
        for s in skills:
            name = s["name"].lower()
            desc = s["description"].lower()
            if q in name or q in desc:
                matched.append(s)

    # ---- 第四步：限制返回数量（只限 full 模式；name 模式返回全部）----
    # 名字很便宜，让 LLM 看全才能发现技能；描述贵，才需要截断
    if detail == "full":
        matched = matched[:MAX_RESULTS_FULL]

    # ---- 第五步：组装输出 ----
    if len(matched) == 0:
        return ok_result(
            tool_call,
            "没有找到匹配 '" + str(query) + "' 的技能。"
            "可以不传 query 列出全部技能名字。",
            {"count": 0},
        )

    # 按 detail 决定每条显示多详细
    lines = []
    for s in matched:
        if detail == "full":
            lines.append("- " + s["name"] + ": " + s["description"])
        else:
            lines.append("- " + s["name"])

    header = "找到 " + str(len(matched)) + " 个技能"
    if query is not None:
        header = header + "（关键词 '" + query + "'）"
    header = header + "，需要详细用法可调 load_skill：\n"

    return ok_result(
        tool_call,
        header + "\n".join(lines),
        {"count": len(matched), "detail": detail},
    )


def schema() -> dict:
    """OpenAI-compatible 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": "search_skills",
            "description": (
                "按关键词搜索可用技能。当你不确定有没有相关技能、"
                "或想看某个领域有哪些技能时调用。"
                "返回匹配的技能，确认要用后再调 load_skill 加载完整说明。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，例如 'pdf' 或 '测试'。不传则列出全部技能名字。",
                    },
                    "detail": {
                        "type": "string",
                        "enum": ["name", "full"],
                        "description": "详细程度：name 只回名字（默认，最省），full 回名字+描述。",
                    },
                },
                "required": [],
            },
        },
    }
