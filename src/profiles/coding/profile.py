"""Coding Profile：Coding Agent 的默认积木组合。

这个文件只负责注册 Coding 专属能力，不负责创建 MachineLoop。

Engine 提供通用能力，Runtime 提供注册表，Profile 决定 Coding Agent
启用哪些积木。CLI 通过 Runtime Factory 使用它。
"""

from src.runtime.memory_provider import MarkdownMemoryProvider
from src.runtime.protected_paths import create_protected_path_check


class MarkdownMemoryExtension:
    """把 Markdown Memory Provider 接到 Runtime。"""

    def __init__(self, provider=None):
        self.provider = provider or MarkdownMemoryProvider()

    def register(self, registry) -> None:
        # 启动时只读一次，保持原来的冻结快照语义。
        value = self.provider.build_injection(registry.context)
        registry.register_injection_value("memory", value)


class CodingProfile:
    """Coding Agent 的默认扩展组合。

    第一版只收拢入口无关的能力：
    - 保护路径检查：所有入口都应该遵守；
    - Markdown Memory：所有入口都使用同一份记忆注入。

    CLI 命令暂时仍由 CLI 注册，因为命令需要 console、当前 history、
    当前 session 等交互状态。等 CommandContext 稳定后再继续收拢。
    """

    def register(self, registry) -> None:
        """向 RuntimeRegistry 注册 Coding 的默认扩展。"""
        workspace = registry.context.workspace

        # 保护 .env、.git 和 .autocoding，避免不同入口各写一套策略。
        registry.register_check(
            "pre_tool",
            create_protected_path_check(workspace),
        )

        # Memory 在启动时读取一次，保持原有的冻结注入语义。
        registry.use(MarkdownMemoryExtension())
