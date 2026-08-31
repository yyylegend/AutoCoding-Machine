"""CLI 输入增强：prompt_toolkit 封装。

【这文件是干什么的】
  把原生 input() 替换成 prompt_toolkit，获得这些能力：
    1. 上下键翻历史（跨会话保留，存在 .autocoding/input_history）
    2. 斜杠命令菜单：输入 / 自动弹出命令选择菜单（右边带说明列）
    3. 命令参数补全：/skill 后补技能名、/resume 后补会话 id
    4. 多行编辑（Alt+Enter 换行，Enter 发送）

【设计原则】
  - 只封装输入，不管输出（输出仍用 rich console）
  - 确认类输入（/compact、ASK 权限）用简单 prompt，不带补全
  - 历史文件独立于 session JSONL，不污染对话历史

【谁会用】
  - cli.py 主输入
  - cli_ui.py 的 ask_permission_confirm
"""

import time
from pathlib import Path

from prompt_toolkit import HTML, PromptSession, prompt
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings


# =====================================
# 命令清单（补全菜单用：命令 + 一句话说明）
# =====================================

# 带参数的命令结尾留一个空格：选中后光标落在参数区，立刻进入参数补全
_COMMANDS_WITH_HELP = [
    ("/plan", "进入 Plan Mode（只读，产出结构化计划）"),
    ("/help", "显示帮助"),
    ("/status", "当前状态（模式/模型/会话/token/上下文占比）"),
    ("/cost", "本次会话 token 消耗"),
    ("/memory", "查看长期记忆（MEMORY.md + USER.md）"),
    ("/clear", "清屏 + 重置对话（JSONL 保留）"),
    ("/compact", "手动压缩上下文（带确认）"),
    ("/skills", "列出可用技能"),
    ("/skill ", "把指定技能注入对话（后接技能名，可补全）"),
    ("/sessions", "列出历史会话"),
    ("/resume ", "切换到指定会话（后接会话 id，可补全）"),
    ("/prompt", "查看当前发给 LLM 的消息结构"),
    ("/quit", "退出（/exit 同义）"),
]

# 会话清单缓存时长（秒）。
# 补全每敲一键都会触发查询，list_sessions 要扫目录读文件，
# 不缓存的话输入会卡；3 秒内的新会话用旧的清单也够用。
_SESSIONS_CACHE_TTL = 3.0


class SlashCompleter(Completer):
    """斜杠命令补全器：三段式。

    1. 输入 / 且还没空格 -> 弹出命令菜单（右边显示一句话说明）
    2. /skill <片段>     -> 补技能名（右边显示技能描述）
    3. /resume <片段>    -> 补会话 id（右边显示会话标题）

    其它情况（普通聊天文字）不提供补全，菜单不会弹出来。
    """

    def __init__(self, skills: list, get_sessions):
        """初始化。

        参数：
          skills       - 技能清单 [{"name", "description"}, ...]
                         （启动时扫描一次，之后不变）
          get_sessions - 返回会话清单的函数 [{"id", "title", "mtime"}, ...]
                         （会话会变，每次现查，内部带 TTL 缓存）
        """
        self.skills = skills
        self.get_sessions = get_sessions
        self._sessions_cache = None
        self._sessions_cache_at = 0.0

    # ---- 会话清单：带 TTL 缓存 ----
    def _cached_sessions(self) -> list:
        now = time.time()
        if self._sessions_cache is None or now - self._sessions_cache_at > _SESSIONS_CACHE_TTL:
            try:
                self._sessions_cache = self.get_sessions() or []
            except Exception:
                # 查会话失败不该影响输入（目录被删之类）
                self._sessions_cache = []
            self._sessions_cache_at = now
        return self._sessions_cache

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor

        # ---- /resume <片段>：补会话 id ----
        if text.startswith("/resume "):
            fragment = text[len("/resume "):]
            for s in self._cached_sessions():
                if s.get("id", "").startswith(fragment):
                    yield Completion(
                        s["id"],
                        start_position=-len(fragment),
                        display_meta=(s.get("title") or "")[:40],
                    )
            return

        # ---- /skill <片段>：补技能名 ----
        if text.startswith("/skill "):
            fragment = text[len("/skill "):]
            for s in self.skills:
                if s.get("name", "").startswith(fragment):
                    yield Completion(
                        s["name"],
                        start_position=-len(fragment),
                        display_meta=(s.get("description") or "")[:40],
                    )
            return

        # ---- 命令本身：输入 / 开头且还没空格 -> 弹命令菜单 ----
        if text.startswith("/") and " " not in text:
            for cmd, desc in _COMMANDS_WITH_HELP:
                if cmd.startswith(text):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display_meta=desc,
                    )


# =====================================
# 多行编辑按键绑定
# =====================================

def _make_bindings() -> KeyBindings:
    """Meta+Enter（Alt+Enter）插入换行，Enter 发送。"""
    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _(event):
        """Alt+Enter：插入换行继续写。"""
        event.current_buffer.insert_text("\n")

    return bindings


# =====================================
# 主输入（带历史 + 命令菜单 + 参数补全 + 多行）
# =====================================

def create_main_session(workspace: Path, skills: list = None, get_sessions=None) -> PromptSession:
    """创建主输入的 PromptSession（整个 CLI 生命周期共用一个）。

    参数：
      workspace    - 项目根目录（历史文件放在 .autocoding/ 下）
      skills       - 技能清单（/skill 补全用；不传就只补命令）
      get_sessions - 返回会话清单的函数（/resume 补全用；不传则该项不补全）

    返回：
      PromptSession 实例

    说明：
      complete_while_typing=True：输入 / 就自动弹出命令选择菜单，
      不用先按 Tab（Tab 手动触发补全也仍然可用）。
    """
    # 历史文件：.autocoding/input_history
    history_dir = workspace / ".autocoding"
    history_dir.mkdir(parents=True, exist_ok=True)
    history_path = history_dir / "input_history"

    completer = SlashCompleter(skills or [], get_sessions or (lambda: []))

    return PromptSession(
        history=FileHistory(str(history_path)),
        completer=completer,
        key_bindings=_make_bindings(),
        multiline=False,             # 默认单行，Alt+Enter 手动换行
        complete_while_typing=True,  # 输入 / 自动弹命令菜单
    )


def main_input(session: PromptSession, plan_mode: bool = False) -> str:
    """主输入：带历史 + 命令菜单 + 参数补全 + Alt+Enter 多行 + 彩色提示符。

    参数：
      session   - create_main_session() 返回的实例
      plan_mode - Plan Mode 标志。True 时提示符加黄色 ⚡ PLAN 徽章

    返回：
      用户输入的字符串

    异常：
      KeyboardInterrupt - 用户按了 Ctrl+C
      EOFError - 用户按了 Ctrl+D
    """
    # 提示符用 prompt_toolkit 的 HTML 上色（不用 rich，输入区只认 prompt_toolkit 格式）
    if plan_mode:
        styled = HTML("<ansiyellow><b>⚡ PLAN</b></ansiyellow> <ansicyan><b>You</b></ansicyan> › ")
    else:
        styled = HTML("<ansicyan><b>You</b></ansicyan> › ")
    return session.prompt(styled)


# =====================================
# 确认输入（简单 prompt，无补全）
# =====================================

def confirm_input(prompt_text: str) -> str:
    """确认类输入：/compact 确认、ASK 权限确认。

    不带补全、不带历史（确认操作不需要翻历史）。
    比主输入轻量，直接用 prompt() 一次性函数。

    参数：
      prompt_text - 提示符

    返回：
      用户输入的字符串（通常很短：""、"y"、"n"）
    """
    return prompt(prompt_text)
