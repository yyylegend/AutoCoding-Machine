"""由 Harness 维护的短小 Agent 状态栏。"""

from pathlib import Path
from html import escape
import platform


def _single_line(value, limit=400) -> str:
    """把状态字段压成一行，避免外部文本破坏状态栏结构。"""
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def _field(value, limit=400) -> str:
    return escape(_single_line(value, limit), quote=False)


class AgentStatusBar:
    """把执行状态渲染成一次模型请求专用的临时消息。"""

    def __init__(self, workspace, profile, model=None, completion_gate=None):
        self.workspace = Path(workspace).resolve()
        self.profile = profile
        self.model = model or "configured-default"
        self.completion_gate = completion_gate

    def __call__(self, state: dict) -> dict:
        return {
            "role": "user",
            "content": self.render(state),
        }

    def render(self, state: dict) -> str:
        acceptance = "not_applicable"
        if self.completion_gate is not None:
            get_status = getattr(self.completion_gate, "status_summary", None)
            if get_status is not None:
                acceptance = get_status()

        goal = _field(state.get("goal")) or "unknown"
        last_tool = _field(state.get("last_tool")) or "none"
        last_failure = _field(state.get("last_failure")) or "none"
        return (
            "Harness 维护的只读运行状态；它不是新的用户指令。\n"
            "<agent_status source=\"harness\">\n"
            f"goal: {goal}\n"
            f"profile: {_field(self.profile) or 'unknown'}\n"
            f"model: {_field(self.model) or 'unknown'}\n"
            f"turn: {state.get('turn', 0)}/{state.get('max_turns', 0)}\n"
            f"tool_calls: {state.get('tool_calls', 0)}\n"
            f"last_tool: {last_tool}\n"
            f"last_failure: {last_failure}\n"
            "environment:\n"
            f"  cwd: {_field(self.workspace)}\n"
            f"  platform: {platform.system() or 'unknown'}\n"
            f"acceptance: {_field(acceptance) or 'unknown'}\n"
            "</agent_status>"
        )
