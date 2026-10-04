"""本地 Markdown 记忆注入；外部记忆接口仍待定义。"""

from auto_coding_machine.memory.local import create_memory_manager, memory_injection


class MarkdownMemoryProvider:
    """复用现有 Markdown Memory，不改变文件格式和读取逻辑。"""

    def build_injection(self, context):
        manager = context.memory_manager or create_memory_manager(context.workspace)
        return memory_injection(manager)
