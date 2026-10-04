import sys

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output.plain_text import PlainTextOutput
from rich.cells import cell_len
from rich.console import Console

from auto_coding_machine.engine.hook_manager import HookManager
from auto_coding_machine.profiles.coding import cli_input, cli_ui
from auto_coding_machine.profiles.coding.llm_adapter import StreamingAdapter


@pytest.fixture
def terminal():
    console = cli_ui.console
    previous_width = console.width
    try:
        yield console
    finally:
        console.width = previous_width


@pytest.mark.parametrize("width", [24, 36, 80, 120])
def test_banner_preserves_wordmark_and_fits_terminal(width, terminal):
    terminal.width = width
    with terminal.capture() as output:
        cli_ui.print_banner()
    text = output.get()
    assert "AutoCoding Machine" in text
    assert "Phase 2.5" not in text
    assert "CLI Demo" not in text
    if width >= 120:
        assert "██╔══██╗" in text
        assert "███╗   ███╗" in text
    elif width <= 36:
        assert "█" not in text
        assert len(text.splitlines()) <= 6
    assert all(cell_len(line) <= width for line in text.splitlines())


@pytest.mark.parametrize("width", [24, 36, 80, 120])
def test_toolbar_fits_width_and_keeps_important_status(width):
    fragments = cli_input.status_fragments({
        "profile": "review",
        "model": "<local>&模型名称非常长" * 5,
        "tokens": 4000,
        "budget": 16000,
        "plan_mode": True,
    }, width=width)

    text = "".join(value for _, value in fragments)
    assert "review" in text
    assert "25%" in text
    assert "PLAN" in text
    assert cell_len(text) <= width


def test_tool_activity_is_plain_text_and_hides_sensitive_content(terminal):
    terminal.width = 100
    hooks = HookManager()
    cli_ui.register_cli_hooks(hooks)

    with terminal.capture() as output:
        hooks.fire("pre_tool", tool_name="read_file", arguments={
            "path": "[bold]orders[/bold].py",
            "api_key": "sensitive-token-value",
        })
        hooks.fire("post_tool", tool_name="read_file", error=False,
                   result_content="private-result-content", duration_ms=12)
    text = output.get()
    assert "[bold]orders[/bold].py" in text
    assert "sensitive-token-value" not in text
    assert "private-result-content" not in text
    assert "read_file" in text
    assert "12ms" in text


def test_permission_prompt_treats_summary_as_plain_text(terminal):
    summary = "write_file [bold]orders[/bold].py"
    with create_pipe_input() as pipe:
        pipe.send_text("n\n")
        with create_app_session(input=pipe, output=PlainTextOutput(sys.stdout)):
            with terminal.capture() as output:
                approved = cli_ui.ask_permission_confirm({
                    "summary": summary,
                    "details": {"path": "[bold]orders[/bold].py"},
                })
    assert approved is False
    assert summary in output.get()


def test_metrics_and_prompt_debug_render_without_missing_imports(terminal):
    terminal.width = 110
    llm = StreamingAdapter([], terminal, cli_ui.THEME, model="gpt-4o")
    llm._update_metrics({"usage": {"prompt_tokens": 100, "completion_tokens": 30}, "ttft_ms": 120})
    llm._update_metrics({})
    llm._update_metrics({"usage": {"prompt_tokens": 100}})
    messages = [{"role": "user", "content": "检查订单查询"}]

    with terminal.capture() as output:
        cli_ui.print_status_bar(llm, messages, 16000)
        cli_ui.print_prompt_debug(messages)
    text = output.get()
    assert "服务端" in text
    assert "用量报告不完整" in text
    assert "输入估算" in text
    assert "检查订单查询" in text


@pytest.mark.parametrize("theme", [cli_ui.THEME, {"dim": "dim", "ai": "cyan"}])
def test_reply_panel_supports_current_and_minimal_themes(theme):
    console = Console(width=100, color_system=None)
    reply = "STREAM_REPLY_SENTINEL"
    with console.capture() as output:
        console.print(cli_ui.agent_reply_panel(reply, theme["ai"], theme=theme))
    assert output.get().count(reply) == 1
    assert "Agent" in output.get()
