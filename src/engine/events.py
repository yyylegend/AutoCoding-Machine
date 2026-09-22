"""Agent 执行事件的稳定数据载体。"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentEvent:
    """一次执行生命周期事件；data 保留事件的具体字段。"""

    name: str
    data: dict[str, Any]
