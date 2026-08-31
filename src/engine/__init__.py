"""通用 Agent 引擎层。

这里放所有 Profile 共用的契约和运行时组件。
Coding / Browser 等具体能力在 src/profiles/ 里。

【重要边界】
  Engine 不依赖具体入口。CLI 通过 Runtime Factory 组装 Coding Profile。

已实现：
  contracts.py          — ToolCall / ToolResult / AgentResponse 等契约
  machine_loop.py       — 核心 while-loop（model_fn 构造注入）
  hook_manager.py       — 生命周期钩子管理器（取代 EventSink）
  permission_manager.py — 只读工具 AUTO，未知工具 DENY
  guard_manager.py      — 连续 3 次相同调用熔断
  context_manager.py    — assemble() 组装 + 安全切分压缩
  session_store.py      — Session 持久化（JSONL 流水账，单一真相源）
  memory_manager.py     — 长期记忆（MEMORY.md / USER.md 读写与注入渲染）
  tool_manager.py       — @tool 装饰器 + ToolManager 注册/分发/权限查询

未实现（计划中）：
  skill_manager.py      — 技能加载与注入
"""

from src.engine.context_manager import ContextManager, assemble
from src.engine.contracts import (
    AgentResponse,
    BudgetPolicy,
    CancellationToken,
    PermissionDecision,
    ToolCall,
    ToolResult,
)
from src.engine.hook_manager import HookManager
from src.engine.guard_manager import GuardManager
from src.engine.machine_loop import MachineLoop
from src.engine.memory_manager import MemoryManager
from src.engine.permission_manager import PermissionManager
from src.engine.session_store import (
    SessionStore,
    latest_session_id,
    list_sessions,
    new_session_id,
    open_session,
    repair_dangling_tool_results,
    sessions_dir_for,
)

__all__ = [
    "MachineLoop",
    "AgentResponse",
    "BudgetPolicy",
    "CancellationToken",
    "ContextManager",
    "GuardManager",
    "HookManager",
    "MemoryManager",
    "PermissionDecision",
    "PermissionManager",
    "SessionStore",
    "ToolCall",
    "ToolResult",
    "assemble",
    "latest_session_id",
    "list_sessions",
    "new_session_id",
    "open_session",
    "repair_dangling_tool_results",
    "sessions_dir_for",
]
