"""run_test 工具：向后兼容别名，内部委托给 run_bash。

【大白话】
  老的模型可能还在用 "run_test" 这个工具名来跑测试，
  为了保持 API 兼容性，这里保留这个名字，
  但实际执行逻辑全部委托给 run_bash.py。

【为什么这样做】
  - 保持现有 API 兼容性（老模型可能还在学 run_test）
  - 统一执行逻辑到 run_bash.py（减少重复代码）
  - run_test 等价于 run_bash，但 schema 描述提示推荐用 run_bash

【权限】
  permission="ask"（需要用户确认），因为执行命令是危险操作。

【谁会用】
  MachineLoop → ToolManager → execute() 调用此工具的 execute 函数
"""

from src.engine.contracts import ToolCall, ToolResult
from src.engine.tool_manager import tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools.helpers import DEFAULT_MAX_OUTPUT_CHARS

# 从同一目录下导入 run_bash 的实际实现
from .run_bash import execute as run_bash_execute


@tool(name="run_test", permission="ask", timeout=180)
def execute(
    tool_call: ToolCall,
    sandbox: WorkspaceSandbox,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
) -> ToolResult:
    """向后兼容别名：内部调用 run_bash 的实际实现。

    目的：
    - 保持现有 API 兼容性（老模型可能还在学 run_test）
    - 统一执行逻辑到 run_bash.py（减少重复代码）
    - 可在 description 中标注"推荐使用 run_bash"

    参数：同 run_bash.execute
        tool_call: 工具调用对象，包含 arguments["command"]
        sandbox: 工作区沙箱，用于路径解析和隔离
        max_output_chars: 最大输出长度，超长会被截断

    返回：同 run_bash.execute
        ToolResult，成功或各种错误类型
    """
    # 直接委托给 run_bash 的实现
    # 这样所有安全检查（白名单、元字符、超时）都由 run_bash 负责
    return run_bash_execute(tool_call, sandbox, max_output_chars)


def schema() -> dict:
    """OpenAI-compatible 工具定义。

    干什么：
        返回符合 OpenAI Function Calling 规范的 schema 字典
        名称仍然是 "run_test"，但 description 提示推荐用 run_bash
    谁调用：
        ToolManager.get_schemas() 会自动调用各模块的 schema()
    """
    return {
        "type": "function",
        "function": {
            "name": "run_test",
            "description": (
                "已弃用别名：建议使用 run_bash 替代。"
                "在 workspace 内执行测试命令。"
                "超时 180 秒。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的测试命令，例如 pytest tests/ -q",
                    },
                },
                "required": ["command"],
            },
        },
    }
