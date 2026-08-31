"""HookManager 阻断检查测试。"""

from src.engine.hook_manager import HookManager


def test_check_defaults_to_allow():
    hooks = HookManager()
    assert hooks.check("pre_tool", tool_name="read_file") == "allow"


def test_check_returns_first_deny_or_ask():
    hooks = HookManager()
    seen = []
    hooks.on_check("pre_tool", lambda **kw: seen.append(kw["tool_name"]) or "allow")
    hooks.on_check("pre_tool", lambda **kw: "deny")
    hooks.on_check("pre_tool", lambda **kw: "ask")

    assert hooks.check("pre_tool", tool_name="write_file") == "deny"
    assert seen == ["write_file"]


def test_check_callback_error_fails_closed():
    hooks = HookManager()

    def broken(**_kwargs):
        raise RuntimeError("broken policy")

    hooks.on_check("pre_tool", broken)

    assert hooks.check("pre_tool") == "deny"
