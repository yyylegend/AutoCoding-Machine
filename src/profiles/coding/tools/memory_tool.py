"""memory 工具：让模型自己维护长期记忆（"一支笔"）。

【大白话】
  模型学到值得记的事（用户纠正、踩坑教训、项目约定），
  就调这个工具往两个"笔记本"里写：
    target="memory" → 项目笔记 .autocoding/MEMORY.md（跟仓库走，git 可见）
    target="user"   → 用户画像 ~/.autocoding/USER.md（跨项目共享）

  三个动作：
    add     — 加一条
    replace — 改一条（old_text 唯一子串定位）
    remove  — 删一条（old_text 唯一子串定位）

【为什么不过沙箱】
  本工具没有路径参数——模型只能选 target，实际文件路径是下面
  代码里写死的。没有模型可控路径就没有越界攻击面，
  所以不需要（也不应该）给 WorkspaceSandbox 开洞。

【权限】
  permission="auto"（自由写）。可控性靠三件事兜底：
  CLI 实时显示写了什么、文件明文可编辑、条目量小随时可审。

【谁会用】
  - MachineLoop → ToolManager → execute()
  - Runtime Profile 借用 build_memory_injection() 做启动注入
"""

from pathlib import Path

from src.config.settings import settings
from src.engine.contracts import ToolCall, ToolResult
from src.engine.memory_manager import MemoryManager
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools.helpers import (
    execution_result,
    get_str_arg,
    invalid_result,
    ok_result,
)


# =====================================
# 路径约定（coding profile 的唯一事实源）
# =====================================

def _user_memory_path() -> Path:
    """USER.md 的固定位置：用户主目录下，跨项目共享。

    单独抽成函数是为了测试能 monkeypatch 掉，
    避免单元测试写到真实的用户主目录。
    """
    return Path.home() / ".autocoding" / "USER.md"


def get_memory_manager(workspace) -> MemoryManager:
    """按 coding profile 的路径约定构造 MemoryManager。

    参数：
      workspace — 项目根目录（MEMORY.md 放在它下面的 .autocoding/ 里）

    返回：
      配置好路径和上限的 MemoryManager

    谁调用：
      本文件的 execute()，以及 Runtime Profile 的启动注入。
    """
    ws = Path(workspace).resolve()
    return MemoryManager(
        memory_path=ws / ".autocoding" / "MEMORY.md",
        user_path=_user_memory_path(),
        memory_limit=settings.MEMORY_CHAR_LIMIT,
        user_limit=settings.USER_CHAR_LIMIT,
    )


def build_memory_injection(workspace):
    """构造启动时的记忆注入消息（冻结快照）。

    返回：
      有记忆内容：{"role": "system", "content": "..."} —— 给 assemble()
      的 dynamic_injections 用；
      记忆为空或开关关闭：None（调用方跳过注入）。

    谁调用：
      Runtime Profile。
    """
    if not settings.MEMORY_ENABLED:
        return None
    manager = get_memory_manager(workspace)
    text = manager.render_injection()
    if text == "":
        return None
    return {"role": "system", "content": text}


# =====================================
# 工具定义
# =====================================

@tool(name="memory", permission="auto")
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int = 10000,
) -> ToolResult:
    """执行 memory 工具。

    参数：
      tool_call.arguments["action"]   — add / replace / remove（必填）
      tool_call.arguments["target"]   — memory / user（必填）
      tool_call.arguments["content"]  — 条目内容（add / replace 必填）
      tool_call.arguments["old_text"] — 定位旧条目的唯一子串（replace / remove 必填）
      sandbox — 只用它的 workspace 属性定位 .autocoding/MEMORY.md，
                不走 resolve()（本工具没有模型可控路径）

    返回：
      成功：ToolResult(content="已记录…")
      参数错：error_type="invalid_args"
      容量满/匹配失败：error_type="execution"（错误信息带当前条目，模型可修正后重试）
    """
    action = get_str_arg(tool_call, "action")
    target = get_str_arg(tool_call, "target")
    content = get_str_arg(tool_call, "content")
    old_text = get_str_arg(tool_call, "old_text")

    # ---- 参数校验 ----
    if action not in ("add", "replace", "remove"):
        return invalid_result(tool_call, "action 只能是 add / replace / remove")
    if target not in ("memory", "user"):
        return invalid_result(tool_call, "target 只能是 memory / user")
    if action in ("add", "replace") and content is None:
        return invalid_result(tool_call, action + " 需要 'content' 参数（条目内容）")
    if action in ("replace", "remove") and old_text is None:
        return invalid_result(tool_call, action + " 需要 'old_text' 参数（定位旧条目的唯一子串）")

    # ---- 分发到 MemoryManager ----
    manager = get_memory_manager(sandbox.workspace)
    if action == "add":
        result = manager.add(target, content)
    elif action == "replace":
        result = manager.replace(target, old_text, content)
    else:
        result = manager.remove(target, old_text)

    # ---- 转成 ToolResult ----
    if result["ok"]:
        return ok_result(tool_call, result["message"], {"target": target, "action": action})
    # 容量满、匹配失败等：算执行类错误，信息里已带修正提示
    return execution_result(tool_call, result["message"])


def schema() -> dict:
    """OpenAI-compatible 工具定义。"""
    return {
        "type": "function",
        "function": {
            "name": "memory",
            "description": (
                "维护你的长期记忆（跨会话保留）。target=memory 记项目笔记"
                "（踩坑教训/常用命令/项目约定），target=user 记用户画像"
                "（偏好/习惯）。条目要短，一行一条。容量满时先 replace 合并"
                "或 remove 旧条目再重试。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "replace", "remove"],
                        "description": "add=加一条；replace=改一条；remove=删一条",
                    },
                    "target": {
                        "type": "string",
                        "enum": ["memory", "user"],
                        "description": "memory=项目笔记；user=用户画像",
                    },
                    "content": {
                        "type": "string",
                        "description": "条目内容（add/replace 用），一句话说清一件事",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "定位旧条目的唯一子串（replace/remove 用），不用写全文",
                    },
                },
                "required": ["action", "target"],
            },
        },
    }
