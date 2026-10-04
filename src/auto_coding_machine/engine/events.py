"""Agent 执行事件的稳定数据载体。"""

import re
from dataclasses import dataclass
from typing import Any

_MAX_PUBLIC_VALUE_LENGTH = 200
_REDACTED = "[REDACTED]"
_HIDDEN_LONG_VALUE = "[HIDDEN: value exceeds 200 characters]"
_PRIVATE_EVENT_FIELDS = {
    "messages", "pending_tool_call", "reply", "result_content",
    "result_metadata",
}
_INLINE_SECRET_PATTERNS = (
    (
        re.compile(
            r"(?i)(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
            r"auth[_-]?token|api[_-]?secret|token|password|passwd|secret|"
            r"authorization)\s*[:=]\s*)"
            r"(?:Bearer\s+)?[^\s,;]+"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)(\bBearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (
        re.compile(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
            r"sk-[A-Za-z0-9_-]{20,}|xox[baprs]-[A-Za-z0-9-]{20,})\b"
        ),
        _REDACTED,
    ),
)


@dataclass(frozen=True)
class AgentEvent:
    """一次执行生命周期事件；data 保留事件的具体字段。"""

    name: str
    data: dict[str, Any]


def _sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if any(marker in normalized for marker in (
        "password", "passwd", "secret", "credential", "authorization",
        "privatekey", "apikey", "accesstoken", "refreshtoken",
        "authtoken", "bearertoken",
    )):
        return True
    return "token" in normalized and not normalized.endswith("tokens") and not any(
        metric in normalized for metric in ("budget", "count", "usage", "limit", "window")
    )


def sanitize_public_value(value: Any, key: str = "") -> Any:
    """Copy JSON-like data while masking likely credentials and long strings."""
    if key and _sensitive_key(key):
        return _REDACTED
    if isinstance(value, str):
        if len(value) > _MAX_PUBLIC_VALUE_LENGTH:
            return _HIDDEN_LONG_VALUE
        for pattern, replacement in _INLINE_SECRET_PATTERNS:
            value = pattern.sub(replacement, value)
        return value
    if isinstance(value, dict):
        return {
            str(item_key): sanitize_public_value(item, str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_public_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[OMITTED]"


def _action_summary(tool_name: str, details: dict[str, Any]) -> str:
    target = next(
        (
            details[key]
            for key in ("path", "file_path", "filename", "target")
            if isinstance(details.get(key), str) and details[key]
        ),
        None,
    )
    return f"{tool_name}: {target}" if target else f"{tool_name} needs permission"


def make_permission_request(
    tool_name: Any,
    tool_call_id: Any,
    arguments: Any,
    turn: Any,
) -> dict[str, Any]:
    """Build the host-visible view without changing execution arguments."""
    safe_name = sanitize_public_value(tool_name, "tool_name")
    safe_name = safe_name if isinstance(safe_name, str) else "unknown"
    details = sanitize_public_value(arguments if isinstance(arguments, dict) else {})
    return {
        "tool_name": safe_name,
        "tool_call_id": sanitize_public_value(tool_call_id, "tool_call_id"),
        "summary": _action_summary(safe_name, details),
        "details": details,
        "turn": sanitize_public_value(turn, "turn"),
    }


def public_event(event: AgentEvent) -> AgentEvent:
    """Project an internal event into a detached host-visible safe view."""
    data = event.data
    if event.name == "permission_required":
        request = make_permission_request(
            data.get("tool_name"),
            data.get("tool_call_id"),
            data.get("arguments"),
            data.get("turn"),
        )
        return AgentEvent(event.name, {"request": request})

    safe_data = {}
    for key, value in data.items():
        if key in _PRIVATE_EVENT_FIELDS:
            continue
        if key == "error" and not isinstance(value, bool):
            continue
        if key == "arguments":
            details = sanitize_public_value(value if isinstance(value, dict) else {})
            name = sanitize_public_value(data.get("tool_name", "tool"), "tool_name")
            name = name if isinstance(name, str) else "unknown"
            safe_data["summary"] = _action_summary(name, details)
            safe_data["details"] = details
            continue
        safe_data[key] = sanitize_public_value(value, key)
    return AgentEvent(event.name, safe_data)
