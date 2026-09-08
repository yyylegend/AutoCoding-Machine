"""验证切换真的更换能力与状态，而不只是改变屏幕上的名称。"""

from io import StringIO
from pathlib import Path

from prompt_toolkit.document import Document
from rich.console import Console
import pytest

from src.profiles.coding.cli_input import SlashCompleter, profile_choices, status_fragments


def test_profile_completion_includes_yaml(tmp_path):
    directory = tmp_path / "profile_configs"
    directory.mkdir()
    (directory / "personal.yaml").write_text("name: personal\nkind: companion\n", encoding="utf-8")
    (directory / "bad.yaml").write_text("[", encoding="utf-8")
    choices = profile_choices(tmp_path)
    completer = SlashCompleter([], lambda: [], choices)
    results = list(completer.get_completions(Document("/profile profile_configs/"), None))
    assert [item.text for item in results] == ["profile_configs/personal.yaml"]
    assert results[0].display_meta_text == "personal"


def test_examples_stay_out_of_profile_menu_until_copied(tmp_path):
    from src.profiles.config import load_profile

    examples = tmp_path / "examples" / "profiles"
    examples.mkdir(parents=True)
    sample = examples / "companion.yaml"
    sample.write_text("name: companion-demo\nkind: companion\n", encoding="utf-8")
    assert [item["name"] for item in profile_choices(tmp_path)] == ["coding", "review", "companion"]
    assert load_profile(str(sample)).name == "companion-demo"
    active = tmp_path / "profile_configs"
    active.mkdir()
    (active / sample.name).write_bytes(sample.read_bytes())
    assert profile_choices(tmp_path)[-1] == {
        "name": "companion-demo", "value": "profile_configs/companion.yaml",
    }


def test_switch_rebuilds_runtime_and_keeps_old_history(tmp_path, monkeypatch):
    from src.profiles.coding import cli, llm_adapter
    from src.profiles.config import load_profile
    from src.engine import list_sessions, open_session

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(cli, "profile_token_budget", lambda _, **kwargs: 10000)
    monkeypatch.setattr(cli, "resolve_context_info", lambda _: {"window": None, "source": "未知"})
    sessions = []
    monkeypatch.setattr(cli, "create_main_session", lambda *args, **kwargs: sessions.append(kwargs))
    inputs = iter(["coding private", "/plan", "/profile companion", "companion private",
                   "/profile missing.yaml", "still companion", "/profile review", "review private", "/quit"])
    modes = []

    def read_input(session, plan_mode):
        modes.append(plan_mode)
        return next(inputs)

    monkeypatch.setattr(cli, "main_input", read_input)
    requests = []

    def respond(messages, **kwargs):
        requests.append((messages, kwargs))
        return {"content": "收到", "tool_calls": [], "usage": {}}

    monkeypatch.setattr(llm_adapter, "chat_stream", respond)
    cli.run_cli()
    assert len(requests) == 4
    assert "coding private" not in str(requests[1][0])
    assert "companion private" not in str(requests[3][0])
    assert "still companion" in str(requests[2][0])
    assert "write_file" in str(requests[0][1]["tools"])
    assert "write_file" not in str(requests[1][1]["tools"])
    assert "run_bash" not in str(requests[3][1]["tools"])
    assert len(sessions) == 3  # 无效配置留在原会话，不启动新运行时。
    assert len({str(item["state_dir"]) for item in sessions}) == 3
    assert modes[2] is True and modes[3] is False  # Plan 状态不会跟到 Companion。
    for name in ("coding", "companion", "review"):
        directory = load_profile(name).state_dir(tmp_path) / "sessions"
        assert len(list_sessions(directory)) == 1
        assert open_session(directory, "")[2] is None


@pytest.mark.parametrize("width", [36, 80, 120])
def test_compact_ui_fits_terminal(width, monkeypatch):
    from src.profiles.coding import cli_ui
    from rich.cells import cell_len

    output = StringIO()
    console = Console(file=output, width=width, color_system=None)
    monkeypatch.setattr(cli_ui, "console", console)
    cli_ui.print_banner()
    cli_ui.print_session_header("companion", "a-long-local-model", "E:/Projects/Coding-Agent", 16000, 2)
    cli_ui.print_profiles([{"name": "companion", "value": "companion"}], "companion")
    cli_ui.print_agent_reply("你好，今天想聊什么？", "default")
    text = output.getvalue()
    # 宽终端显示原来的渐变 Logo，窄终端显示原来的紧凑 Panel。
    if width >= 86:
        assert "Coding Agent" in text
        assert "Phase 2.5" in text
    else:
        assert "AutoCoding" in text
    assert all(cell_len(line) <= width for line in text.splitlines())


def test_toolbar_keeps_names_as_plain_text():
    fragments = status_fragments({"profile": "review", "model": "<local>&qwen", "tokens": 4000, "budget": 16000})
    text = "".join(value for _, value in fragments)
    assert "<local>&qwen" in text
    assert "25%" in text
