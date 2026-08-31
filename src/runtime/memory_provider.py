"""Runtime 记忆 Provider。"""

from src.profiles.coding.tools.memory_tool import build_memory_injection


class MemoryProvider:
    """记忆注入的最小接口。"""

    def build_injection(self, context):
        raise NotImplementedError


class MarkdownMemoryProvider(MemoryProvider):
    """复用现有 Markdown Memory，不改变文件格式和读取逻辑。"""

    def build_injection(self, context):
        return build_memory_injection(context.workspace)
