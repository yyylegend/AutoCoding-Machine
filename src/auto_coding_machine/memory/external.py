"""外部长期记忆的只读契约；不依赖任何具体记忆服务。"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class MemoryScope:
    """一次召回的明确身份范围；各字段都参与精确隔离。"""

    user_id: str
    team_id: str | None = None
    agent_id: str | None = None

    def __post_init__(self):
        if not self.user_id or not self.user_id.strip():
            raise ValueError("user_id 不能为空")
        if self.team_id is not None and not self.team_id.strip():
            raise ValueError("team_id 不能为空字符串")
        if self.agent_id is not None and not self.agent_id.strip():
            raise ValueError("agent_id 不能为空字符串")


@dataclass(frozen=True)
class MemoryQuery:
    text: str
    scope: MemoryScope
    max_items: int
    max_tokens: int
    timeout_seconds: float

    def __post_init__(self):
        if self.max_items <= 0 or self.max_tokens <= 0 or self.timeout_seconds <= 0:
            raise ValueError("召回条数、token 预算与超时必须大于零")


@dataclass(frozen=True)
class MemoryHit:
    text: str
    source: str
    scope: MemoryScope


@dataclass(frozen=True)
class MemoryCapture:
    """一轮已完成对话中，调用方显式选择写回的最小内容。"""

    scope: MemoryScope
    session_id: str
    user_message: str
    assistant_message: str
    timeout_seconds: float = 10

    def __post_init__(self):
        if not self.session_id.strip():
            raise ValueError("session_id 不能为空")
        if not self.user_message.strip() or not self.assistant_message.strip():
            raise ValueError("写回需要非空的用户输入和最终回复")
        if self.timeout_seconds <= 0:
            raise ValueError("写回超时必须大于零")


class MemoryProvider(Protocol):
    """只读召回插槽。实现方必须按 scope 隔离并执行 timeout_seconds。"""

    def recall(self, query: MemoryQuery) -> list[MemoryHit]: ...


class MemoryWriter(Protocol):
    """显式写回插槽，默认不启用。"""

    def capture(self, turn: MemoryCapture) -> None: ...


class MemoryWriteError(Exception):
    """远端明确拒绝或无法确认写入的业务错误。"""


class MemoryWriteUncertainError(MemoryWriteError):
    """请求可能已被接收但未拿到确认；禁止自动重试以免重复。"""


class InMemoryMemoryProvider:
    """无网络测试替身；只返回同一作用域且匹配查询的条目。"""

    def __init__(self, hits=()):
        self.hits = list(hits)

    def recall(self, query: MemoryQuery) -> list[MemoryHit]:
        term = query.text.casefold().strip()
        if not term:
            return []
        return [
            hit for hit in self.hits
            if hit.scope == query.scope and term in hit.text.casefold()
        ][:query.max_items]
