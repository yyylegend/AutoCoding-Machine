"""Coding 工具包（只读 + 写操作）。

层级：
  src/profiles/coding/
    sandbox.py          # 路径沙箱
    tools/              # 工具实现
      __init__.py       # CodingTools 统一入口
      helpers.py
      read_file.py      # 只读
      list_dir.py       # 只读
      glob_tool.py      # 只读
      grep.py           # 只读
      write_file.py      # 写操作（ASK）
      edit_file.py       # 写操作（ASK）
      run_test.py        # 写操作（ASK）= run_bash 别名
      run_bash.py        # 写操作（ASK）白名单 bash 工具
      memory_tool.py     # 长期记忆（AUTO）MEMORY.md / USER.md

【怎么用】
  from src.profiles.coding.tools import CodingTools

  tools = CodingTools(workspace=".")
  result = tools.execute(ToolCall(
      id="call_1",
      name="read_file",
      arguments={"path": "src/main.py"},
  ))
"""

from src.config.settings import settings
from src.engine.contracts import ToolCall, ToolResult
from src.engine.tool_manager import ToolManager
from src.profiles.coding.sandbox import WorkspaceSandbox

from . import edit_file
from . import glob_tool
from . import grep
from . import list_dir
from . import read_file
from . import run_bash
from . import run_test
from . import write_file
from . import load_skill    # 新增：技能渐进式披露（L2 按需加载）
from . import search_skills # 新增：技能搜索（Discovery 层，代码侧筛选省 token）
from . import memory_tool   # 新增：长期记忆（MEMORY.md / USER.md）
from . import recall_history # 新增：会话历史召回（BM25 检索 JSONL 原文）
from .helpers import DEFAULT_MAX_OUTPUT_CHARS


class CodingTools:
    """Coding 工具集合（只读 + 写操作）。

    现在它只是 ToolManager 的薄适配器：
      1. 负责创建 WorkspaceSandbox 并注册 7 个 coding 工具
      2. execute / get_schemas 直接委托给内部的 ToolManager
      3. 对外接口（构造参数、方法签名）保持不变，老代码零改动
    """

    def __init__(self, workspace, max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS):
        """初始化。

        参数：
          workspace        — 允许操作的项目根目录
          max_output_chars — 单次工具输出最大字符数
        """
        self.sandbox = WorkspaceSandbox(workspace)
        self.max_output_chars = max_output_chars

        # 真正的注册和分发都交给 ToolManager
        # 工具名从各模块 @tool 装饰器读取，不再手写 name -> 模块 字典
        self._manager = ToolManager(self.sandbox, max_output_chars)
        # 固定注册顺序 = schema 顺序，方便测试和 prompt cache 稳定
        # run_bash 在 run_test 之后注册，方便老模型优先匹配 run_test
        modules = [read_file, list_dir, glob_tool, grep,
                   write_file, edit_file, run_test, run_bash,
                   load_skill, search_skills]
        # 记忆开关关闭时不注册 memory 工具（模型看不到也调不到）
        if settings.MEMORY_ENABLED:
            modules.append(memory_tool)
        # recall_history 始终注册（纯只读，无副作用）
        modules.append(recall_history)
        for module in modules:
            self._manager.register(module)

    def execute(self, tool_call: ToolCall) -> ToolResult:
        """根据 tool_call.name 分发到具体工具。

        这样 MachineLoop 只需要：
          tools.execute(tool_call)

        未知工具由 ToolManager 返回 invalid_args 错误。
        """
        return self._manager.execute(tool_call)

    def get_schemas(self) -> list:
        """返回 OpenAI-compatible 工具 schema 列表。

        顺序 = __init__ 里的注册顺序。
        MachineLoop 调 LLM 时会用到。
        """
        return self._manager.get_schemas()

    def get_manager(self):
        """暴露内部的 ToolManager。

        谁用：
          PermissionManager 需要它来读各工具 @tool 装饰器声明的权限。
          不传的话 PermissionManager 只认硬编码白名单，
          新工具（memory / search_skills 等）会被当未知工具 DENY。
        """
        return self._manager


__all__ = [
    "CodingTools",
]
