import asyncio
from dataclasses import replace

import pytest
from textual.widgets import Switch

from auto_coding_machine.profiles.config import Profile
from auto_coding_machine.profiles.coding.tui.app import CodingApp
from auto_coding_machine.profiles.coding.tui.widgets import Composer, PermissionScreen
from auto_coding_machine.runtime.factory import build_runtime_components


def local_profile():
    return Profile(context_budget=8000, skills=(), load_instructions=False)


async def ready(app, pilot):
    for _ in range(100):
        await pilot.pause(0.02)
        if not app.busy:
            assert app.session is not None
            return
    raise AssertionError("会话未完成启动")


def test_completion_verification_can_be_explicitly_disabled(tmp_path):
    profile = replace(local_profile(), verify_on_stop=True)
    disabled = build_runtime_components(tmp_path, profile, verify_on_stop=False)
    enabled = build_runtime_components(tmp_path, profile, verify_on_stop=True)
    assert disabled.completion_gate is None
    assert enabled.completion_gate is not None


@pytest.mark.parametrize("size", [(120, 40), (80, 24), (40, 20)])
def test_textual_starts_with_verification_off_and_resizes(tmp_path, size):
    async def scenario():
        app = CodingApp(tmp_path, local_profile())
        async with app.run_test(size=size) as pilot:
            await ready(app, pilot)
            assert app.components.completion_gate is None
            assert app.query_one("#verify-switch", Switch).value is False
            assert app.query_one("#composer", Composer).region.width > 0
            assert app.query_one("#send").region.right <= size[0]
            await pilot.resize_terminal(100, 30)
            await pilot.pause()
            assert app.query_one("#sidebar").display
    asyncio.run(scenario())


def test_textual_verification_toggle_keeps_session_history(tmp_path):
    async def scenario():
        app = CodingApp(tmp_path, local_profile())
        async with app.run_test(size=(120, 40)) as pilot:
            await ready(app, pilot)
            app.session.append_message({"role": "user", "content": "保留我的历史"})
            session_id = app.session.store.session_id
            await pilot.press("f2")
            await ready(app, pilot)
            assert app.components.completion_gate is not None
            assert app.session.store.session_id == session_id
            assert "保留我的历史" in str(app.session.history)
            await app.submit("/plan")
            await app.submit("/clear")
            await pilot.press("f2")
            await ready(app, pilot)
            assert app.components.completion_gate is None
            assert app.session.history == []
            assert app.session.runtime.permission.plan_mode
            assert "保留我的历史" in str(app.session.store.load())
    asyncio.run(scenario())


def test_textual_multiline_completion_and_permission_keyboard(tmp_path):
    async def scenario():
        app = CodingApp(tmp_path, local_profile())
        async with app.run_test(size=(120, 40)) as pilot:
            await ready(app, pilot)
            composer = app.query_one("#composer", Composer)
            await pilot.press("/", "v", "e", "tab")
            assert composer.text == "/verify "
            composer.clear()
            await pilot.press("a", "alt+enter", "b")
            assert composer.text == "a\nb"
            decisions = []
            app.push_screen(PermissionScreen({"summary": "write_file [bold]path[/bold]",
                                             "details": {"path": "notes.md"}}), decisions.append)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert decisions == [False]
    asyncio.run(scenario())


@pytest.mark.parametrize("confirm", ["tab", "enter"])
def test_slash_menu_arrows_select_command_without_moving_input_focus(tmp_path, confirm):
    async def scenario():
        app = CodingApp(tmp_path, local_profile())
        async with app.run_test(size=(80, 24)) as pilot:
            await ready(app, pilot)
            composer = app.query_one("#composer", Composer)
            await pilot.press("/")
            await pilot.press("down", "down", "up", confirm)
            assert composer.text == "/status "
            assert app.focused is composer
            assert not app.query_one("#completion-menu").display
            assert app.session.history == []
            composer.load_text("first\nsecond")
            composer.move_cursor((1, 3))
            await pilot.press("up")
            assert composer.cursor_location == (0, 3)
    asyncio.run(scenario())


def test_textual_commands_history_and_invalid_profile_keep_current_session(tmp_path):
    async def scenario():
        app = CodingApp(tmp_path, local_profile())
        async with app.run_test(size=(80, 24)) as pilot:
            await ready(app, pilot)
            original = app.session.store.session_id
            await app.submit("/help")
            await app.submit("/profile missing.yaml")
            assert app.session.store.session_id == original
            await app.submit("/plan")
            assert app.session.runtime.permission.plan_mode
            await app.submit("/exit")
            assert not app.session.runtime.permission.plan_mode
            await app.submit("/verify on")
            await ready(app, pilot)
            assert app.components.completion_gate is not None
            await app.submit("/status")
            await pilot.press("ctrl+up")
            assert app.query_one("#composer", Composer).text == "/status"
            assert (local_profile().state_dir(tmp_path) / "input_history").is_file()
    asyncio.run(scenario())


def test_textual_resume_restores_real_jsonl_without_deleting_clear_history(tmp_path):
    async def scenario():
        app = CodingApp(tmp_path, local_profile())
        async with app.run_test(size=(120, 40)) as pilot:
            await ready(app, pilot)
            app.session.append_message({"role": "user", "content": "我的项目资料"})
            original = app.session.store.session_id
            await app.submit("/clear")
            assert app.session.history == []
            assert "我的项目资料" in str(app.session.store.load())
            await app.submit(f"/resume {original}")
            await ready(app, pilot)
            assert "我的项目资料" in str(app.session.history)
    asyncio.run(scenario())


def test_textual_profile_switch_isolates_history_and_capabilities(tmp_path):
    configs = tmp_path / "profile_configs"
    configs.mkdir()
    (configs / "companion.yaml").write_text(
        "name: local-companion\nkind: companion\ncontext_budget: 8000\nskills: []\n"
        "tools: []\nload_instructions: false\n", encoding="utf-8",
    )
    async def scenario():
        app = CodingApp(tmp_path, local_profile())
        async with app.run_test(size=(120, 40)) as pilot:
            await ready(app, pilot)
            app.session.append_message({"role": "user", "content": "coding private"})
            store = app.session.store
            await app.submit("/profile profile_configs/companion.yaml")
            await ready(app, pilot)
            assert app.profile.name == "local-companion"
            assert app.session.history == []
            assert app.session.runtime.tools.get_schemas() == []
            assert app.query_one("#verify-switch").disabled
            assert "coding private" in str(store.load())
    asyncio.run(scenario())


def test_textual_real_connection_failure_stops_task_without_retrying(tmp_path):
    async def scenario():
        app = CodingApp(tmp_path, local_profile())
        async with app.run_test(size=(120, 40)) as pilot:
            await ready(app, pilot)
            app.model.base_url = "http://127.0.0.1:1"
            app.model.timeout = 0.1
            await app.submit("读取项目说明")
            await ready(app, pilot)
            assert app.blocked
            assert app.agent_run is None
            assert app.last_result is None
            assert app._worker.error is not None
            assert not app.model.usage_complete
            assert app.session.store.load()[-1]["content"] == "读取项目说明"
    asyncio.run(scenario())


def test_textual_cancel_dismisses_permission_and_signals_real_run(tmp_path):
    async def scenario():
        app = CodingApp(tmp_path, local_profile())
        async with app.run_test(size=(120, 40)) as pilot:
            await ready(app, pilot)
            run = app.session.runtime.create_run(app.session.messages)
            app.agent_run = run
            app._set_busy(True, "等待批准")
            decisions = []
            app.push_screen(PermissionScreen({"summary": "write_file notes.md", "details": {}}), decisions.append)
            await pilot.pause()
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert run.cancel_token.is_cancelled()
            assert decisions == [False]
            app.agent_run = None
            app._set_busy(False)
    asyncio.run(scenario())


def test_textual_missing_resume_can_recover_by_opening_new_session(tmp_path):
    async def scenario():
        app = CodingApp(tmp_path, local_profile(), resume="missing-session")
        async with app.run_test(size=(80, 24)) as pilot:
            for _ in range(100):
                await pilot.pause(0.02)
                if not app.busy:
                    break
            assert app.blocked and app.session is None
            await app.submit("/new")
            await ready(app, pilot)
            assert not app.blocked
    asyncio.run(scenario())
