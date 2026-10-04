from functools import partial
from pathlib import Path

from prompt_toolkit.document import Document
from prompt_toolkit.history import FileHistory
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.message import Message
from textual.theme import Theme
from textual.widgets import Button, Footer, Label, LoadingIndicator, Markdown, OptionList, Select, Static, Switch
from textual.widgets.option_list import Option
from textual.worker import Worker, WorkerState

from auto_coding_machine.engine import list_sessions, open_session
from auto_coding_machine.engine.events import sanitize_public_value
from auto_coding_machine.profiles.config import Profile, load_profile
from auto_coding_machine.profiles.coding.cli_input import SlashCompleter, profile_choices
from auto_coding_machine.profiles.coding.plan_mode import get_plan_mode_injection
from auto_coding_machine.runtime.factory import build_runtime_components, open_harness_session
from auto_coding_machine.runtime.prompts import build_injections, load_instructions
from auto_coding_machine.runtime.skills import load_skill_content
from auto_coding_machine.runtime.state import migrate_legacy_coding_state

from .model import TuiModelAdapter
from .widgets import Brand, Composer, PermissionScreen, ToolActivity


COMMANDS = {
    "/help": "命令与快捷键", "/status": "会话与预算", "/cost": "本次任务用量",
    "/sessions": "会话清单", "/resume": "恢复会话", "/new": "新建会话",
    "/profile": "选择配置", "/plan": "只读计划模式", "/verify": "完成验证 on / off",
    "/skills": "技能清单", "/skill": "加载技能", "/memory": "查看长期记忆",
    "/prompt": "请求消息结构", "/compact": "压缩上下文", "/clear": "清屏并重置视图",
    "/quit": "退出程序", "/exit": "退出计划模式或程序",
}


class SessionLoaded(Message):
    def __init__(self, profile, session, model, components, repaired, preserve_view=False):
        super().__init__()
        self.profile = profile
        self.preserve_view = preserve_view
        self.session, self.model, self.components, self.repaired = session, model, components, repaired


class HarnessEvent(Message):
    def __init__(self, event):
        super().__init__()
        self.event = event


class ModelStream(Message):
    def __init__(self, kind, value):
        super().__init__()
        self.kind, self.value = kind, value


class TaskFinished(Message):
    def __init__(self, result):
        super().__init__()
        self.result = result


class CodingApp(App):
    TITLE = "AUTOCODING MACHINE"
    CSS_PATH = "styles.tcss"
    BINDINGS = [
        Binding("ctrl+c", "cancel_task", "取消任务", priority=True),
        Binding("ctrl+q", "request_quit", "退出", priority=True),
        Binding("ctrl+b", "sidebar", "会话栏", priority=True),
        Binding("f1", "help", "帮助", priority=True),
        Binding("f2", "toggle_verify", "完成验证", priority=True),
    ]

    def __init__(self, workspace=None, profile="coding", resume=None, *, verify_on_stop=False):
        super().__init__()
        self.workspace = Path(workspace or Path.cwd()).resolve()
        self.profile = profile if isinstance(profile, Profile) else load_profile(profile)
        self.resume = resume
        self.verify_on_stop = verify_on_stop
        self.session = None
        self.model = None
        self.components = None
        self.agent_run = None
        self.busy = True
        self.blocked = False
        self.plan_mode = False
        self.last_result = None
        self._worker = None
        self._tools = {}
        self._stream_widget = None
        self._stream_text = ""
        self._stream_dirty = False
        self._history = []
        self._history_index = 0
        self._history_draft = ""
        self._input_history = None
        self._completion_values = []
        self._sidebar_override = None
        self.register_theme(Theme(
            name="autocoding", primary="#d4a574", secondary="#83a598",
            accent="#d4a574", foreground="#e5ddd0", background="#191c1a",
            surface="#222723", panel="#262d28", success="#a8bd8a", warning="#d9b778",
            error="#d58377", dark=True,
        ))
        self.theme = "autocoding"

    def compose(self) -> ComposeResult:
        yield Static(Text("◆ AUTOCODING MACHINE", style="bold #d4a574"), id="topbar")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("运行配置", classes="section-label")
                choices = profile_choices(self.workspace)
                yield Select([(item["name"], item["value"]) for item in choices],
                             allow_blank=False, value=Select.NULL, id="profile-select")
                yield Label("完成验证 · 实验功能", classes="section-label")
                yield Switch(False, id="verify-switch")
                yield Static("默认关闭。开启后检查代码修改的验证证据。", classes="muted")
                yield Label("历史会话", classes="section-label")
                yield Button("＋ 新会话", id="new-session")
                yield OptionList(id="sessions")
            with Vertical(id="main"):
                yield Static("正在打开会话…", id="session-info", markup=False)
                with VerticalScroll(id="transcript"):
                    yield Brand(id="brand")
                    yield Static("读取项目、审查代码或描述一个任务。\n输入 / 查看命令；权限操作会单独请求批准。",
                                 id="welcome", markup=False)
                yield OptionList(id="completion-menu")
                with Horizontal(id="activity"):
                    yield LoadingIndicator(id="loading")
                    yield Static("正在打开会话", id="activity-text", markup=False)
                yield Composer(id="composer", show_line_numbers=False, soft_wrap=True)
                with Horizontal(id="actions"):
                    yield Static("Enter 发送 · Alt+Enter 换行 · Tab 补全 · Ctrl+↑ 历史", id="input-hint")
                    yield Button("取消", id="cancel", disabled=True)
                    yield Button("发送", id="send", variant="primary", disabled=True)
                yield Static("", id="status", markup=False)
        yield Footer()

    def on_mount(self):
        self.query_one("#completion-menu").display = False
        self.set_interval(0.08, self._flush_stream)
        self._set_busy(True, "正在打开会话")
        self._open_profile(self.profile, self.resume)

    def on_resize(self, event: Resize):
        if self._sidebar_override is None:
            self.query_one("#sidebar").display = event.size.width >= 100

    def _set_busy(self, value, activity="就绪"):
        self.busy = value
        for selector in ("#composer", "#send", "#profile-select", "#new-session", "#sessions"):
            self.query_one(selector).disabled = value
        self.query_one("#verify-switch").disabled = value or self.profile.kind != "coding"
        self.query_one("#cancel").disabled = not value or self.agent_run is None
        self.query_one("#loading").display = value
        self.query_one("#activity-text", Static).update(Text(activity))
        if not value:
            self.query_one("#composer").focus()

    def _open_profile(self, profile, resume=None, *, preserve_view=False):
        self._set_busy(True, "正在打开会话")
        self._worker = self.run_worker(
            partial(self._load_session, profile, resume, preserve_view), thread=True, group="setup",
            exit_on_error=False,
        )

    def _load_session(self, profile, resume, preserve_view):
        migrate_legacy_coding_state(self.workspace)
        if preserve_view:
            store = self.session.store
        else:
            store, _, error = open_session(profile.state_dir(self.workspace) / "sessions", resume)
            if error:
                raise ValueError(error)
        components = build_runtime_components(
            self.workspace, profile, session_store=store,
            verify_on_stop=self.verify_on_stop and profile.kind == "coding",
        )
        model = TuiModelAdapter(
            components.tools.get_schemas(),
            lambda kind, value: self.post_message(ModelStream(kind, value)),
            session_id=store.session_id,
            model=profile.model, completion_gate=components.completion_gate,
        )
        instructions = load_instructions(self.workspace) if profile.load_instructions else {"global": None, "project": None}
        skills = components.tools.sandbox.skills if "load_skill" in profile.tools else []
        session = open_harness_session(
            self.workspace, model.call, profile=profile, session_store=store,
            runtime_components=components, base_injections=build_injections(instructions, skills),
            on_event=lambda event: self.post_message(HarnessEvent(event)),
        )
        repaired = session.repair_interrupted() if resume is not None and not preserve_view else 0
        self.post_message(SessionLoaded(profile, session, model, components, repaired, preserve_view))

    async def on_session_loaded(self, message: SessionLoaded):
        keep_plan = (
            self.plan_mode and self.session is not None and self.profile == message.profile
            and self.session.store.session_id == message.session.store.session_id
        )
        if message.preserve_view:
            self.session.runtime = message.session.runtime
            self.session.rebuild()
        else:
            self.session = message.session
        self.model, self.components = message.model, message.components
        self.profile = message.profile
        self.plan_mode = keep_plan
        if keep_plan:
            self.session.runtime.permission.plan_mode = True
            self.session.set_extra_injections([{"role": "system", "content": get_plan_mode_injection()}])
        self.agent_run = None
        self.blocked = False
        self.last_result = None
        self._tools.clear()
        self._stream_widget = None
        self._stream_text = ""
        self._stream_dirty = False
        self.profile.state_dir(self.workspace).mkdir(parents=True, exist_ok=True)
        self._input_history = FileHistory(str(self.profile.state_dir(self.workspace) / "input_history"))
        self._history = list(reversed(list(self._input_history.load_history_strings())))
        self._history_index = len(self._history)
        self.completer = SlashCompleter(
            self.components.tools.sandbox.skills, self._list_sessions, profile_choices(self.workspace),
        )
        await self._replay_history()
        if message.repaired:
            await self._notice(f"已补齐 {message.repaired} 条执行状态未知的工具回执；请检查实际操作结果。")
        switch = self.query_one("#verify-switch", Switch)
        switch.value = self.components.completion_gate is not None
        choices = profile_choices(self.workspace)
        select = self.query_one("#profile-select", Select)
        value = next((item["value"] for item in choices if item["name"] == self.profile.name), None)
        if value is None:
            choices.append({"name": self.profile.name, "value": "__current__"})
            value = "__current__"
        select.set_options([(item["name"], item["value"]) for item in choices])
        select.value = value
        self._refresh_sessions()
        self._set_busy(False)
        self._refresh_status()

    def _list_sessions(self):
        return list_sessions(self.profile.state_dir(self.workspace) / "sessions")

    def _refresh_sessions(self):
        options = self.query_one("#sessions", OptionList)
        options.clear_options()
        for item in self._list_sessions():
            marker = "● " if item["id"] == self.session.store.session_id else "  "
            options.add_option(Option(Text(marker + item["id"]), id=item["id"]))

    def _refresh_status(self):
        if self.session is None:
            return
        tokens = self.components.context_manager.count_request_tokens(self.session.messages)
        budget = self.components.token_budget
        pressure = f"{tokens:,}/{budget:,}" if budget else f"{tokens:,} / 未知"
        verification = "开启" if self.components.completion_gate else "关闭"
        mode = " · PLAN" if self.plan_mode else ""
        self.query_one("#session-info", Static).update(Text(
            f"{self.profile.name} / {self.model.model}  ·  {self.session.store.session_id}\n{self.workspace}",
        ))
        self.query_one("#status", Static).update(Text(
            f"输入估算 {pressure} · 完成验证 {verification}{mode}",
        ))

    async def _notice(self, text, *, error=False):
        widget = Static(Text(text), classes="notice error" if error else "notice")
        await self.query_one("#transcript").mount(widget)
        self.query_one("#transcript").scroll_end(animate=False)

    async def _replay_history(self):
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.remove_children()
        if not self.session.history:
            await transcript.mount(Brand(id="brand"), Static(
                "描述一个任务开始。/help 查看命令与快捷键。", id="welcome", markup=False,
            ))
        for item in self.session.history:
            content = item.get("content") or ""
            if item.get("role") == "user" and content:
                await transcript.mount(Static(Text("❯ " + content), classes="user-message"))
            elif item.get("role") == "assistant" and content:
                await transcript.mount(Markdown(content, classes="agent-message"))
        transcript.scroll_end(animate=False)

    async def on_composer_submitted(self, message: Composer.Submitted):
        await self.submit(message.text)

    async def submit(self, text):
        if self.busy or not text.strip():
            return
        self.query_one("#composer", Composer).clear()
        self.query_one("#completion-menu").display = False
        if self.session is None:
            if text == "/new":
                self._open_profile(self.profile)
            elif text in ("/quit", "/exit"):
                self.action_request_quit()
            else:
                await self._notice("会话尚未打开；使用 /new 新建，或在侧栏选择配置。", error=True)
            return
        self._input_history.append_string(text)
        self._history.append(text)
        self._history_index = len(self._history)
        if text.startswith("/"):
            try:
                await self._command(text)
            except (ValueError, OSError) as exc:
                await self._notice(str(sanitize_public_value(str(exc))), error=True)
            return
        if self.blocked:
            await self._notice("当前会话需要检查；使用 /resume <id> 恢复或 /new 新建会话。", error=True)
            return
        self._tools.clear()
        self._stream_widget = None
        self._stream_text = ""
        self.last_result = None
        self.model.reset_metrics()
        self.agent_run = self.session.begin_run(text)
        await self.query_one("#transcript").mount(Static(Text("❯ " + text), classes="user-message"))
        self._run_task(self.agent_run.start)

    def _run_task(self, function):
        self._set_busy(True, "正在请求模型")
        self._worker = self.run_worker(partial(self._execute, function), thread=True,
                                       group="task", exit_on_error=False)

    def _execute(self, function):
        self.post_message(TaskFinished(function()))

    async def on_task_finished(self, message: TaskFinished):
        result = message.result
        self.last_result = result
        if result["status"] == "permission_required":
            self.query_one("#activity-text", Static).update("等待你的批准")
            self.query_one("#loading").display = False
            self.push_screen(PermissionScreen(result["permission_request"]), self._permission_decision)
            return
        self._flush_stream()
        reply = result.get("reply")
        if reply:
            if self._stream_widget is not None:
                await self._stream_widget.update(reply)
            else:
                await self.query_one("#transcript").mount(Markdown(reply, classes="agent-message"))
        self.blocked = result.get("error") == "session_write_failed"
        if not self.blocked:
            self.session.refresh()
        if self.blocked:
            await self._notice("会话写入失败，任务已停止。工具可能已经生效，请检查实际状态后恢复会话。", error=True)
        elif result["status"] not in ("success", "need_input"):
            await self._notice("任务已取消" if result["status"] == "cancelled"
                               else f"任务结束：{result.get('error', result['status'])}",
                               error=result["status"] == "failed")
        self.agent_run = None
        self._set_busy(False, {"success": "任务完成", "need_input": "等待补充信息",
                               "cancelled": "任务已取消", "failed": "任务失败"}.get(result["status"], "就绪"))
        self._refresh_status()
        self._refresh_sessions()
        self.query_one("#transcript").scroll_end(animate=False)

    def _permission_decision(self, approved):
        if self.agent_run is not None:
            self._run_task(partial(self.agent_run.resolve_permission, approved=approved))

    async def on_harness_event(self, message: HarnessEvent):
        event = message.event
        data = event.data
        if event.name == "pre_tool":
            call_id = data["tool_call_id"]
            activity = ToolActivity(call_id, data["tool_name"], data.get("details", {}))
            self._tools[call_id] = activity
            await self.query_one("#transcript").mount(activity)
            self.query_one("#activity-text", Static).update(Text(f"请求工具：{data['tool_name']}"))
        elif event.name == "post_tool":
            activity = self._tools.get(data["tool_call_id"])
            if activity is None:
                activity = ToolActivity(data["tool_call_id"], data["tool_name"], {})
                await self.query_one("#transcript").mount(activity)
            activity.finish(data)
        elif event.name == "permission_resolved":
            self.query_one("#activity-text", Static).update("已批准" if data["decision"] == "approved" else "已拒绝")
        elif event.name in ("compacted", "compaction_fallback", "completion_rejected"):
            await self._notice({"compacted": "上下文已压缩", "compaction_fallback": "摘要不可用，使用历史摘录",
                                "completion_rejected": "完成验证需要补充证据"}[event.name])

    async def on_model_stream(self, message: ModelStream):
        if message.kind == "start":
            self._flush_stream()
            self._stream_widget = None
            self._stream_text = ""
            self._stream_dirty = False
            self.query_one("#activity-text", Static).update("模型正在生成" if message.value else "正在等待完成验证")
        elif message.kind == "chunk":
            if self._stream_widget is None:
                self._stream_widget = Markdown("", classes="agent-message")
                await self.query_one("#transcript").mount(self._stream_widget)
            self._stream_text += message.value
            self._stream_dirty = True
        elif message.kind == "end":
            self._flush_stream()

    def _flush_stream(self):
        if self._stream_dirty and self._stream_widget is not None:
            self._stream_widget.update(self._stream_text)
            self._stream_dirty = False
            self.query_one("#transcript").scroll_end(animate=False)

    async def on_worker_state_changed(self, event: Worker.StateChanged):
        if event.state != WorkerState.ERROR:
            return
        # 界面只报告异常类别，不输出可能含凭据的请求详情。
        error = event.worker.error
        response = getattr(error, "response", None)
        detail = f"HTTP {response.status_code}，请检查密钥、订阅和模型配置" if response is not None else type(error).__name__
        await self._notice(f"操作失败：{detail}。当前任务已停止，使用 /new 或 /resume 重新打开会话。", error=True)
        self.blocked = True
        self.agent_run = None
        self._set_busy(False, "操作失败")

    def action_cancel_task(self):
        if self.agent_run is None:
            if not self.busy:
                self.notify("当前没有运行中的任务；Ctrl+Q 退出。")
            return
        self.agent_run.cancel()
        self.query_one("#activity-text", Static).update("已请求取消，等待当前操作结束")
        if isinstance(self.screen, PermissionScreen):
            self.screen.dismiss(False)

    def action_request_quit(self):
        if self.busy:
            self.action_cancel_task()
            self.notify("等待当前操作结束后，再按 Ctrl+Q 退出。")
            return
        self.exit()

    def on_unmount(self):
        if self.agent_run is not None:
            self.agent_run.cancel()

    def action_sidebar(self):
        sidebar = self.query_one("#sidebar")
        sidebar.display = not sidebar.display
        self._sidebar_override = sidebar.display

    async def action_help(self):
        await self._notice("\n".join(f"{name}  {description}" for name, description in COMMANDS.items()))

    def action_toggle_verify(self):
        if not self.busy and self.profile.kind == "coding":
            self.query_one("#verify-switch", Switch).toggle()

    def on_switch_changed(self, event: Switch.Changed):
        if self.session is None or self.busy:
            return
        if event.value == (self.components.completion_gate is not None):
            return
        self.verify_on_stop = event.value
        self._open_profile(self.profile, self.session.store.session_id, preserve_view=True)

    def on_select_changed(self, event: Select.Changed):
        if self.busy or event.value is Select.NULL or event.value == "__current__":
            return
        selected = load_profile(str(self.workspace / event.value)) if event.value not in ("coding", "review", "companion") else load_profile(event.value)
        if self.session is None or selected != self.profile:
            self._open_profile(selected)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected):
        if self.busy:
            return
        if event.option_list.id == "sessions":
            self._open_profile(self.profile, event.option.id)
        elif event.option_list.id == "completion-menu":
            self._accept_completion(event.option_index)

    async def on_button_pressed(self, event: Button.Pressed):
        if event.button.id == "send":
            await self.submit(self.query_one("#composer", Composer).text.strip())
        elif event.button.id == "cancel":
            self.action_cancel_task()
        elif event.button.id == "new-session" and not self.busy:
            self._open_profile(self.profile)

    def on_text_area_changed(self, event: Composer.Changed):
        if self.session is None:
            return
        text = event.text_area.text
        completions = list(self.completer.get_completions(Document(text), None)) if text.startswith("/") else []
        menu = self.query_one("#completion-menu", OptionList)
        self._completion_values = [(text[:len(text) + item.start_position] + item.text) for item in completions]
        # 新命令沿用输入层的补全协议。
        if text.startswith("/") and " " not in text:
            self._completion_values = [name for name in COMMANDS if name.startswith(text)]
        menu.clear_options().add_options([Option(Text(value)) for value in self._completion_values])
        menu.highlighted = 0 if self._completion_values else None
        menu.display = bool(self._completion_values)

    def _accept_completion(self, index):
        if index < len(self._completion_values):
            self.query_one("#composer", Composer).load_text(self._completion_values[index] + " ")
            self.query_one("#completion-menu").display = False
            self.query_one("#composer").focus()

    def on_composer_complete(self):
        if self._completion_values:
            selected = self.query_one("#completion-menu", OptionList).highlighted
            self._accept_completion(selected if selected is not None else 0)

    def on_composer_history(self, event: Composer.History):
        if self._history_index == len(self._history):
            self._history_draft = self.query_one("#composer", Composer).text
        self._history_index = min(len(self._history), max(0, self._history_index + event.direction))
        text = self._history_draft if self._history_index == len(self._history) else self._history[self._history_index]
        self.query_one("#composer", Composer).load_text(text)

    async def _command(self, text):
        name, _, argument = text.partition(" ")
        argument = argument.strip()
        if name in ("/quit", "/exit"):
            if self.plan_mode and name == "/exit":
                self.plan_mode = False
                self.session.runtime.permission.plan_mode = False
                self.session.set_extra_injections()
                self._refresh_status()
            else:
                self.action_request_quit()
        elif name == "/help":
            await self.action_help()
        elif name == "/profile":
            if argument:
                selected = load_profile(argument if argument in ("coding", "review", "companion") else str(self.workspace / argument))
                self._open_profile(selected)
            else:
                self._sidebar_override = True
                self.query_one("#sidebar").display = True
                self.query_one("#profile-select").focus()
        elif name in ("/resume", "/new"):
            if name == "/resume" and not argument:
                await self._notice("\n".join(item["id"] for item in self._list_sessions()) or "没有历史会话")
            else:
                self._open_profile(self.profile, argument if name == "/resume" else None)
        elif name == "/sessions":
            await self._notice("\n".join(item["id"] for item in self._list_sessions()) or "没有历史会话")
        elif name == "/verify":
            if self.profile.kind != "coding":
                await self._notice("完成验证只用于 Coding Profile。")
            elif argument in ("on", "off"):
                self.query_one("#verify-switch", Switch).value = argument == "on"
            else:
                await self._notice("完成验证：" + ("开启" if self.components.completion_gate else "关闭") + "；/verify on|off")
        elif name == "/plan":
            if self.profile.kind != "coding":
                await self._notice("当前 Profile 不使用 Coding Plan Mode。")
            else:
                self.plan_mode = True
                self.session.runtime.permission.plan_mode = True
                self.session.set_extra_injections([{"role": "system", "content": get_plan_mode_injection()}])
                self._refresh_status()
        elif name == "/clear":
            self.session.clear()
            await self._replay_history()
            self._refresh_status()
        elif name == "/compact":
            self.push_screen(PermissionScreen({"summary": "压缩当前上下文视图，JSONL 历史保留", "details": {}}, confirmation=True), self._compact_decision)
        elif name == "/status":
            self._refresh_status()
            info = self.components.context_info or {}
            last_input = self.model.last_prompt_tokens
            await self._notice(
                f"Profile：{self.profile.name}\n模型：{self.model.model}\n会话：{self.session.store.session_id}\n"
                f"模型窗口：{info.get('window') or '未知'} · 来源：{info.get('source', '未知')}\n"
                f"输入预算：{self.components.token_budget}\n"
                f"上次服务端输入：{last_input if last_input is not None else '未报告'}\n工具："
                + ", ".join(schema["function"]["name"] for schema in self.session.runtime.tools.get_schemas()),
            )
        elif name == "/cost":
            await self._notice(f"本次任务已报告用量：输入 {self.model.prompt_tokens} · 输出 {self.model.completion_tokens}" + ("\n服务端用量报告不完整" if not self.model.usage_complete else ""))
        elif name == "/skills":
            await self._notice("\n".join(item["name"] for item in self.components.tools.sandbox.skills) or "没有可用技能")
        elif name == "/skill":
            content = load_skill_content(self.components.tools.sandbox.skills, argument)
            if content is None:
                await self._notice("没有找到该技能。", error=True)
            else:
                self.session.append_message({"role": "user", "content": f"[技能注入] {argument}\n{content}"})
                await self._notice(f"已加载技能：{argument}")
        elif name == "/memory":
            manager = self.components.tools.sandbox.memory_manager
            for path in (manager.memory_path, manager.user_path):
                await self._notice(f"{path}\n" + (path.read_text(encoding="utf-8") if path.is_file() else "文件不存在"))
        elif name == "/prompt":
            await self._notice("\n".join(f"{index}: {item.get('role')} · {len(str(item.get('content') or ''))} 字符" for index, item in enumerate(self.session.messages)))
        else:
            await self._notice("未知命令；/help 查看可用命令。", error=True)

    def _compact_decision(self, approved):
        if approved:
            self._set_busy(True, "正在压缩上下文")
            self._worker = self.run_worker(self._compact, thread=True, group="compact", exit_on_error=False)

    def _compact(self):
        self.session.compact()
        self.call_from_thread(self._set_busy, False, "上下文压缩完成")
        self.call_from_thread(self._refresh_status)
