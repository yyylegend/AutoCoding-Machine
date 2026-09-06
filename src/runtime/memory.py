"""共享记忆路径与注入；不依赖任何 Coding 工具或终端界面。"""

from pathlib import Path

from src.config.settings import settings
from src.engine.memory_manager import MemoryManager


def create_memory_manager(workspace, *, state_dir=None, user_path=None):
    """显式传入状态目录即可隔离 Profile；默认路径兼容旧 Coding。"""
    root = Path(state_dir) if state_dir is not None else Path(workspace).resolve() / ".autocoding"
    if user_path is None:
        user_path = root / "USER.md" if state_dir is not None else Path.home() / ".autocoding" / "USER.md"
    return MemoryManager(
        memory_path=root / "MEMORY.md", user_path=user_path,
        memory_limit=settings.MEMORY_CHAR_LIMIT, user_limit=settings.USER_CHAR_LIMIT,
    )


def memory_injection(manager):
    if not settings.MEMORY_ENABLED:
        return None
    text = manager.render_injection()
    return {"role": "system", "content": text} if text else None
