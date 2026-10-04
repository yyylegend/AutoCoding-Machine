import json

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, OptionList, Static, TextArea

from auto_coding_machine.engine.events import sanitize_public_value
from auto_coding_machine.profiles.coding.cli_ui import _LOGO_COLORS, _LOGO_LINES


class Composer(TextArea):
    BINDINGS = [
        Binding("enter", "submit", "发送", priority=True),
        Binding("alt+enter,shift+enter", "newline", "换行", priority=True),
        Binding("tab", "complete", "补全", priority=True),
        Binding("ctrl+up", "history(-1)", "上一条", priority=True),
        Binding("ctrl+down", "history(1)", "下一条", priority=True),
    ]

    class Submitted(Message):
        def __init__(self, text):
            self.text = text
            super().__init__()

    class Complete(Message):
        pass

    class History(Message):
        def __init__(self, direction):
            self.direction = direction
            super().__init__()

    def action_submit(self):
        if not self.disabled and self.app.query_one("#completion-menu").display:
            self.post_message(self.Complete())
            return
        if not self.disabled and self.text.strip():
            self.post_message(self.Submitted(self.text.strip()))

    def action_newline(self):
        self.insert("\n")

    def action_complete(self):
        self.post_message(self.Complete())

    def action_history(self, direction):
        self.post_message(self.History(direction))

    def _move_completion(self, direction):
        menu = self.app.query_one("#completion-menu", OptionList)
        if not menu.display or not menu.option_count:
            return False
        if direction < 0:
            menu.action_cursor_up()
        else:
            menu.action_cursor_down()
        return True

    def action_cursor_up(self, select=False):
        if select or not self._move_completion(-1):
            super().action_cursor_up(select)

    def action_cursor_down(self, select=False):
        if select or not self._move_completion(1):
            super().action_cursor_down(select)


class Brand(Static):
    def render(self):
        if self.size.width < max(len(line) for line in _LOGO_LINES):
            return Text("◆ AUTOCODING MACHINE", style="bold #d4a574")
        wordmark = Text()
        for index, (line, color) in enumerate(zip(_LOGO_LINES, _LOGO_COLORS)):
            if index:
                wordmark.append("\n")
            wordmark.append(line, style=f"bold {color}")
        return wordmark


class PermissionScreen(ModalScreen[bool]):
    BINDINGS = [("escape", "reject", "拒绝")]

    def __init__(self, request, *, confirmation=False):
        super().__init__()
        self.request = request
        self.confirmation = confirmation

    def compose(self) -> ComposeResult:
        with Vertical(id="permission-dialog"):
            yield Static("操作确认" if self.confirmation else "需要你的批准", classes="dialog-heading")
            yield Static(Text(self.request["summary"]), id="permission-summary")
            with VerticalScroll(id="permission-details"):
                yield Static(Text(json.dumps(self.request["details"], ensure_ascii=False, indent=2)))
            yield Static("批准表示允许尝试执行；操作仍可能失败。", classes="muted")
            with Horizontal(classes="dialog-actions"):
                yield Button("拒绝 / Esc", id="reject")
                yield Button("批准", variant="warning", id="approve")

    def on_mount(self):
        self.query_one("#reject", Button).focus()

    def on_button_pressed(self, event: Button.Pressed):
        self.dismiss(event.button.id == "approve")

    def action_reject(self):
        self.dismiss(False)


class ToolActivity(Collapsible):
    def __init__(self, call_id, name, details):
        self.call_id = call_id
        self.tool_name = name
        self.result_label = Static("等待权限检查或工具执行", classes="muted")
        safe = sanitize_public_value(details)
        super().__init__(
            Static(Text(json.dumps(safe, ensure_ascii=False, indent=2))),
            self.result_label, title=f"› {name} · 请求操作", collapsed=True,
            classes="tool-activity",
        )

    def finish(self, data):
        error_type = data.get("error_type")
        if error_type == "cancelled":
            status = "未执行 · 已取消"
        elif data.get("error"):
            status = "已拒绝" if error_type in ("permission", "hook_denied") else f"失败 · {error_type}"
        else:
            status = f"完成 · {data.get('duration_ms', 0)}ms"
        self.title = f"{self.tool_name} · {status}"
        self.result_label.update(Text(status))
