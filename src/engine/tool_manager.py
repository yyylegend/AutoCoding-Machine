"""工具管理器：@tool 装饰器 + ToolManager。

【这文件是干什么的】
  1. @tool 装饰器：给工具模块的 execute 函数挂上元数据
     （名字、权限级别、超时），不改变函数本身的行为。
  2. ToolManager：统一注册、分发、查询工具。
     以前 CodingTools 里的 name -> 模块 字典就是它的雏形，
     现在抽出来放到 engine 层，方便 coding / browser 等 Profile 共用。

【大白话】
  工具模块自己声明“我叫什么、要不要问用户”：

      @tool(name="write_file", permission="ask")
      def execute(tool_call, sandbox, max_output_chars): ...

  ToolManager 负责收编这些模块：

      manager = ToolManager(sandbox)
      manager.register(write_file)          # 注册
      manager.get_schemas()                 # 给 LLM 看的工具清单
      manager.execute(tool_call)            # 按名字分发执行
      manager.get_permission("write_file")  # PermissionManager 来问权限

【谁会用】
  - src/profiles/coding/tools/__init__.py：CodingTools 内部改用它
  - src/engine/permission_manager.py：check() 优先读装饰器声明的权限
  - tests/test_tool_manager.py：单元测试
"""

from src.engine.contracts import ToolCall, ToolResult


def tool(name, permission="auto", timeout=120):
    """装饰器：把工具元数据挂到 execute 函数上。

    参数：
      name       — 工具名，必须和该模块 schema() 里的 name 一致
      permission — 权限级别："auto"（直接执行）/ "ask"（先问用户）/ "deny"（拒绝）
      timeout    — 单次执行超时秒数（当前只记录，后续 Loop 用）

    返回：
      装饰器函数。被装饰的 execute 行为完全不变，
      只是多了一个 _tool_meta 属性供 ToolManager 读取。

    谁调用：
      各工具模块（如 read_file.py）在 def execute 上方使用。
    """
    def decorator(func):
        # 只挂属性，不包一层，避免影响调用签名和 docstring
        func._tool_meta = {
            "name": name,
            "permission": permission,
            "timeout": timeout,
        }
        return func

    return decorator


class ToolManager:
    """工具管理器：注册工具模块，按名字分发执行。

    用法例子：
        manager = ToolManager(WorkspaceSandbox("."))
        manager.register(read_file)
        result = manager.execute(ToolCall(id="1", name="read_file",
                                          arguments={"path": "src/main.py"}))
    """

    def __init__(self, sandbox, max_output_chars: int = 10000):
        """初始化。

        参数：
          sandbox          — 传给每个工具 execute 的沙箱（如 WorkspaceSandbox）
          max_output_chars — 单次工具输出最大字符数
        """
        self.sandbox = sandbox
        self.max_output_chars = max_output_chars
        # name -> 工具模块（模块里必须有 execute 和 schema 两个函数）
        self._tools = {}
        # name -> 装饰器元数据字典；没装饰器的工具存 None
        self._metas = {}

    def register(self, module) -> None:
        """注册一个工具模块。

        参数：
          module — 工具模块，要求有 execute(tool_call, sandbox, max_output_chars)
                   和 schema() 两个函数

        名字来源（按优先级）：
          1. execute 上的 @tool 装饰器元数据
          2. 向后兼容：没装饰器时读 schema()["function"]["name"]

        谁调用：
          CodingTools.__init__ 等 Profile 初始化时。
        """
        meta = getattr(module.execute, "_tool_meta", None)
        if meta is not None:
            name = meta["name"]
        else:
            # 老工具还没加装饰器，从 schema 里读名字，保持兼容
            name = module.schema()["function"]["name"]

        self._tools[name] = module
        self._metas[name] = meta

    def is_registered(self, tool_name) -> bool:
        """判断某个工具是否已注册。

        PermissionManager 用它区分“未注册”和“注册了但权限是 deny”，
        未注册时才回落到自己的硬编码规则。
        """
        return tool_name in self._tools

    def get_schemas(self) -> list:
        """返回所有已注册工具的 OpenAI-compatible schema 列表。

        顺序 = 注册顺序（dict 保序），方便测试和 prompt cache 稳定。
        """
        schemas = []
        for module in self._tools.values():
            schemas.append(module.schema())
        return schemas

    def execute(self, tool_call: ToolCall) -> ToolResult:
        """根据 tool_call.name 分发到具体工具执行。

        参数：
          tool_call — 模型下的工单

        返回：
          工具的 ToolResult；未知工具返回 error_type="invalid_args"。

        谁调用：
          CodingTools.execute / MachineLoop。
        """
        name = ""
        if tool_call.name is not None:
            name = str(tool_call.name).strip()

        tool_module = self._tools.get(name)
        if tool_module is None:
            return ToolResult(
                tool_call_id=tool_call.id,
                content="未知工具: " + name,
                error=True,
                error_type="invalid_args",
                retryable=False,
            )

        return self._run_tool(tool_module, tool_call)

    def _run_tool(self, tool_module, tool_call: ToolCall) -> ToolResult:
        """实际执行已注册的工具模块。"""
        return tool_module.execute(
            tool_call,
            self.sandbox,
            self.max_output_chars,
        )

    def get_permission(self, tool_name) -> str:
        """返回工具声明的权限级别字符串。

        规则：
          - 未注册的工具    -> "deny"（不认识的一律拒绝）
          - 注册了但没装饰器 -> "auto"（老工具默认放行，兼容期行为）
          - 有装饰器        -> 装饰器里声明的 permission

        谁调用：
          PermissionManager.check。
        """
        if tool_name not in self._tools:
            return "deny"

        meta = self._metas.get(tool_name)
        if meta is None:
            return "auto"

        return meta["permission"]
