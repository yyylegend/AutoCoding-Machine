"""load_skill 工具：按需加载某个技能的完整说明（渐进式披露的 L2 层）。

【大白话】
  启动时模型只看到技能清单（名字 + 一句话描述，很省 token）。
  当模型判断"这个任务需要某个技能"时，就调这个工具，
  把那个技能的 SKILL.md 完整正文加载进来再照着做。

  这就是渐进式披露：不需要的技能不占上下文，需要时才展开。

【为什么 permission="auto"】
  这个工具只是读一个已知技能目录里的文本文件，不写任何东西、
  不执行命令，完全只读且安全，所以直接放行，不用问用户。

【谁会用】
  MachineLoop → ToolManager → execute() 调用此工具的 execute 函数
"""

from src.engine.contracts import ToolCall, ToolResult
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.skills import discover_skills, load_skill_content
from src.profiles.coding.tools.helpers import (
    get_str_arg,
    invalid_result,
    ok_result,
)


@tool(name="load_skill", permission="auto")
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int,
) -> ToolResult:
    """执行 load_skill：按名字加载技能的完整说明。

    参数：
      tool_call.arguments["name"] — 技能名（要和清单里的名字一致）

    成功：
      content = 该技能 SKILL.md 的正文（可能被截断）

    失败：
      invalid_args — 没传 name，或名字不在可用清单里
    """
    # ---- 第一步：读取 name 参数 ----
    name = get_str_arg(tool_call, "name")
    if name is None:
        return invalid_result(tool_call, "load_skill 需要参数 name（技能名）")

    # ---- 第二步：扫描技能目录，拿到当前可用的技能清单 ----
    # sandbox.workspace 是项目根目录，用来定位项目级技能目录
    skills = discover_skills(sandbox.workspace)

    # ---- 第三步：按名字加载技能正文 ----
    content = load_skill_content(skills, name)
    if content is None:
        # 没找到：把可用技能名列出来，方便模型纠正名字
        available = ", ".join(s["name"] for s in skills)
        if not available:
            available = "（当前没有发现任何技能）"
        return invalid_result(
            tool_call,
            "没找到技能 '" + name + "'。可用技能：" + available,
        )

    # ---- 第四步：成功返回技能正文 ----
    return ok_result(
        tool_call,
        content,
        {"name": name, "chars": len(content)},
    )


def schema() -> dict:
    """OpenAI-compatible 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": "load_skill",
            "description": (
                "按名字加载某个技能的完整使用说明。"
                "当你从技能清单里看到某个技能可能有用、但需要它的详细用法时调用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "要加载的技能名，必须和技能清单里的名字一致",
                    }
                },
                "required": ["name"],
            },
        },
    }
