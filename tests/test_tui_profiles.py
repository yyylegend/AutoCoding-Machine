"""验证切换真的更换能力与状态，而不只是改变屏幕上的名称。"""

import asyncio
from pathlib import Path

from prompt_toolkit.document import Document

from auto_coding_machine.profiles.coding.cli_input import SlashCompleter, profile_choices
from tests.helpers import ready


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
    from auto_coding_machine.profiles.config import load_profile

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


def _open_app(app):
    async def scenario():
        async with app.run_test(size=(120, 40)) as pilot:
            await ready(app, pilot)
    asyncio.run(scenario())


def test_resume_repairs_tool_with_unknown_execution_state(tmp_path, monkeypatch):
    from auto_coding_machine.engine import SessionStore
    from auto_coding_machine.profiles.coding.tui.app import CodingApp
    from auto_coding_machine.profiles.config import load_profile
    from auto_coding_machine.runtime import factory

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    monkeypatch.setattr(factory, "resolve_context_info", lambda _model: {"window": None, "source": "未知"})
    monkeypatch.setattr(factory, "profile_token_budget", lambda _profile, **_kwargs: 10000)
    store = SessionStore(load_profile("review").state_dir(tmp_path) / "sessions", "dangling")
    store.append({"role": "user", "content": "读文件"})
    store.append({
        "role": "assistant",
        "tool_calls": [{
            "id": "call-1", "type": "function",
            "function": {"name": "read_file", "arguments": "{}"},
        }],
    })

    _open_app(CodingApp(tmp_path, "review", resume="dangling"))

    assert store.load()[-1]["tool_call_id"] == "call-1"
    assert "执行状态未知" in store.load()[-1]["content"]


def test_cli_uses_factory_context_info_once(tmp_path, monkeypatch):
    from auto_coding_machine.profiles.coding.tui.app import CodingApp
    from auto_coding_machine.runtime import factory

    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    resolved = []
    monkeypatch.setattr(factory, "resolve_context_info", lambda model: resolved.append(model) or {
        "window": 16000, "source": "test",
    })
    monkeypatch.setattr(factory, "profile_token_budget", lambda _profile, **_kwargs: 8000)

    _open_app(CodingApp(tmp_path, "review"))

    assert len(resolved) == 1
