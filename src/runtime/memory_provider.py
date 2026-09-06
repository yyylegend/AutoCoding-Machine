"""Runtime 记忆 Provider。"""

from src.runtime.memory import create_memory_manager, memory_injection


class MemoryProvider:
    """记忆注入的最小接口。"""

    def build_injection(self, context):
        raise NotImplementedError


class MarkdownMemoryProvider(MemoryProvider):
    """复用现有 Markdown Memory，不改变文件格式和读取逻辑。"""

    def build_injection(self, context):
        manager = context.memory_manager or create_memory_manager(context.workspace)
        return memory_injection(manager)
