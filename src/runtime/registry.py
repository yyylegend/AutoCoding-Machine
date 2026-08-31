"""Runtime 注册表。

第一版只做简单、显式的注册，不扫描未知 Python 文件。
它把工具、钩子和动态注入放到同一个小接口里，方便 Profile 组装。
"""

from dataclasses import dataclass
from typing import Any, Callable

from src.common.logger import get_logger

logger = get_logger("runtime.registry")


@dataclass
class RuntimeContext:
    """扩展能看到的最小运行时上下文。"""

    workspace: Any
    profile: str = "coding"


class RuntimeRegistry:
    """Runtime 的插孔面板。

    扩展通过 register() 拿到这个对象，不能直接依赖具体入口。
    """

    def __init__(self, context: RuntimeContext, tools=None, hooks=None):
        self.context = context
        self.tools = tools
        self.hooks = hooks
        self._injections: list[tuple[str, Callable]] = []
        self._commands: dict[str, dict] = {}

    def register_tool(self, module) -> None:
        """注册一个现有工具模块。"""
        if self.tools is None:
            raise RuntimeError("Runtime 尚未提供工具管理器")
        self.tools.register(module)

    def register_hook(self, event: str, callback: Callable) -> None:
        """注册观察型钩子，复用当前 HookManager。"""
        if self.hooks is None:
            raise RuntimeError("Runtime 尚未提供 HookManager")
        self.hooks.on(event, callback)

    def register_check(self, event: str, callback: Callable) -> None:
        """注册可阻断检查，复用当前 HookManager。"""
        if self.hooks is None:
            raise RuntimeError("Runtime 尚未提供 HookManager")
        self.hooks.on_check(event, callback)

    def register_injection(self, name: str, provider: Callable) -> None:
        """注册一个动态注入提供器。"""
        if any(item[0] == name for item in self._injections):
            raise ValueError(f"Runtime injection 已注册: {name}")
        self._injections.append((name, provider))

    def register_injection_value(self, name: str, value) -> None:
        """注册一个已经构造好的注入。

        适合 Instructions、Skills 和 Memory 这类需要冻结快照的内容。
        """
        self.register_injection(name, lambda _context, value=value: value)

    def get_injection_names(self) -> list[str]:
        """返回注册顺序，便于状态显示和测试。"""
        return [name for name, _provider in self._injections]

    def register_command(self, name: str, description: str, handler: Callable) -> None:
        """预留斜杠命令插孔，第一轮只保存注册信息。"""
        if name in self._commands:
            raise ValueError(f"Runtime command 已注册: {name}")
        self._commands[name] = {"description": description, "handler": handler}

    def use(self, extension) -> None:
        """启用一个内置扩展。"""
        register = getattr(extension, "register", None)
        if register is None:
            raise TypeError("Extension 必须提供 register(runtime) 方法")
        register(self)

    def build_injections(self) -> list[dict]:
        """按注册顺序构造动态注入。

        单个扩展失败只跳过自己的注入，并记录 warning。
        """
        result = []
        for name, provider in self._injections:
            try:
                value = provider(self.context)
            except Exception as exc:
                logger.warning("Runtime injection 失败：%s，error=%s", name, exc)
                continue
            if value is None:
                continue
            if isinstance(value, list):
                result.extend(item for item in value if item)
            else:
                result.append(value)
        return result

    def get_command(self, name: str):
        """按名称查找命令；找不到时返回 None。"""
        return self._commands.get(name)

    def run_command(self, name: str, context) -> bool:
        """运行一个已注册命令，返回是否找到。"""
        command = self.get_command(name)
        if command is None:
            return False
        command["handler"](context)
        return True

    def get_commands(self) -> dict[str, dict]:
        """返回命令注册信息，供帮助显示和测试使用。"""
        return dict(self._commands)
