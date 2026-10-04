"""CLI 终端 UI：所有跟"好不好看"有关的函数都在这里。

【这文件是干什么的】
  把 cli.py 里跟终端显示相关的函数抽出来，让 cli.py 只管逻辑。

  包含：
    - 主题色定义（THEME）
    - 全局 Console 实例
    - Banner（欢迎界面）
    - Help（帮助面板）
    - 状态栏（TTFT / token / 上下文压力）
    - 事件输出（ConsoleEventSink）
    - 各种辅助渲染（分隔线、技能清单、/prompt 调试）

【谁会用】
  cli.py（from cli_ui import ...）
"""

import sys
from datetime import datetime

from rich.console import Console, Group
from rich.cells import cell_len
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from auto_coding_machine.common.token_utils import get_token_count
from auto_coding_machine.engine.events import sanitize_public_value
from auto_coding_machine.engine.hook_manager import HookManager


# ============================================================
# 全局 Console + 主题色
# ============================================================

# Windows 默认控制台编码是 GBK，先切到 UTF-8，否则 ⏺ › 等符号会报编码错误
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass  # 重定向等特殊场景下可能失败，不影响主流程

# 全局 Console：整个 CLI 共用一个，保证 Spinner 和普通输出不打架
console = Console()

# 主题色（集中定义，改配色只改这里）
THEME = {
    "primary": "default",        # 标题跟随终端背景
    "accent": "#d4a574",          # 暖色强调
    "success": "green",          # 成功
    "warning": "yellow",         # 警告 / 需要输入
    "error": "red",              # 错误
    "dim": "dim",                # 次要信息
    "tool": "#8bd5ca",           # 工具名称
    "user": "#d4a574",           # 用户消息
    "ai": "bright_black",        # 回答使用轻边框
}


# ============================================================
# Banner（欢迎界面）
# ============================================================


_LOGO_LINES = [
    " █████╗ ██╗   ██╗████████╗ ██████╗  ██████╗ ██████╗ ██████╗ ██╗███╗   ██╗ ██████╗ ",
    "██╔══██╗██║   ██║╚══██╔══╝██╔═══██╗██╔════╝██╔═══██╗██╔══██╗██║████╗  ██║██╔════╝ ",
    "███████║██║   ██║   ██║   ██║   ██║██║     ██║   ██║██║  ██║██║██╔██╗ ██║██║  ███╗",
    "██╔══██║██║   ██║   ██║   ██║   ██║██║     ██║   ██║██║  ██║██║██║╚██╗██║██║   ██║",
    "██║  ██║╚██████╔╝   ██║   ╚██████╔╝╚██████╗╚██████╔╝██████╔╝██║██║ ╚████║╚██████╔╝",
    "╚═╝  ╚═╝ ╚═════╝    ╚═╝    ╚═════╝  ╚═════╝ ╚═════╝ ╚═════╝ ╚═╝╚═╝  ╚═══╝ ╚═════╝ ",
    "███╗   ███╗ █████╗  ██████╗██╗  ██╗██╗███╗   ██╗███████╗",
    "████╗ ████║██╔══██╗██╔════╝██║  ██║██║████╗  ██║██╔════╝",
    "██╔████╔██║███████║██║     ███████║██║██╔██╗ ██║█████╗  ",
    "██║╚██╔╝██║██╔══██║██║     ██╔══██║██║██║╚██╗██║██╔══╝  ",
    "██║ ╚═╝ ██║██║  ██║╚██████╗██║  ██║██║██║ ╚████║███████╗",
    "╚═╝     ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝╚═╝╚═╝  ╚═══╝╚══════╝",
]

_LOGO_COLORS = [
    "#00e5ff", "#00c8ff", "#00aaff", "#3d8bff", "#6a6aff", "#8a4fff",
    "#9a3fef", "#aa33d9", "#c02bbf", "#d424a4", "#e91e8c", "#ff1477",
]


def print_banner():
    """宽终端保留渐变大字 Logo，窄终端使用紧凑品牌标题。"""
    console.print()
    logo_width = max(cell_len(line) for line in _LOGO_LINES)
    if console.width >= logo_width:
        for line, color in zip(_LOGO_LINES, _LOGO_COLORS):
            console.print(Text(line, style=f"bold {color}", no_wrap=True))
        console.print()
    title = Text()
    title.append("◆ ", style=THEME["accent"])
    title.append("AutoCoding Machine", style=f"bold {THEME['primary']}")
    console.print(title)
    console.print(Text("输入任务开始 · /help 查看命令", style=THEME["dim"]))
    console.print()


def print_session_header(profile, model, workspace, budget, skill_count, resumed=False):
    identity = Text(f"  {profile}", style=f"bold {THEME['accent']}")
    identity.append(f"  /  {model or '未设置模型'}", style=THEME["dim"])
    identity.no_wrap = True
    identity.overflow = "ellipsis"
    directory = Text(f"  {workspace}", style=THEME["dim"], no_wrap=True, overflow="ellipsis")
    budget_text = f"{budget:,}" if budget is not None else "未知"
    state = Text(
        f"  {'恢复会话' if resumed else '新会话'} · 输入预算 {budget_text} · Skills {skill_count}",
        style=THEME["dim"],
    )
    console.print(Panel(
        Group(identity, directory, state),
        title=Text("会话", style=THEME["accent"]),
        title_align="left",
        border_style=THEME["ai"],
        padding=(0, 0),
        width=min(console.width, 86),
    ))
    console.print()


def print_profiles(choices, current):
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style=THEME["accent"])
    table.add_column()
    for item in choices:
        table.add_row("● " + item["name"] if item["name"] == current else item["name"], Text(item["value"]))
    console.print(Text(f"当前配置 · {current}", style=f"bold {THEME['primary']}"))
    console.print(table)
    console.print(Text("/profile <名称或 YAML 路径> · 切换时开新会话，旧对话保留", style=THEME["dim"]))


# ============================================================
# Help / 技能清单 / 调试
# ============================================================


def print_help():
    """按使用目的组织命令，让启动帮助保持紧凑。"""
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column(style=f"bold {THEME['accent']}")
    table.add_column(style=THEME["dim"])
    rows = (
        ("/profile", "查看或切换配置"),
        ("/plan · /exit", "进入或退出只读计划模式"),
        ("/status · /cost", "运行状态和本次用量"),
        ("/sessions · /resume", "查看或恢复历史会话"),
        ("/skills · /skill", "查看或加载技能"),
        ("/memory · /prompt", "查看记忆或请求消息"),
        ("/compact · /clear", "压缩上下文或清屏"),
        ("/help · /quit", "查看帮助或退出"),
    )
    for command, description in rows:
        table.add_row(command, description)
    console.print(Group(
        Text("命令", style=f"bold {THEME['primary']}"),
        table,
        Text("Enter 发送 · Alt+Enter 换行 · Tab 补全", style=THEME["dim"]),
    ))
    console.print()


def print_skills(skills: list):
    """打印技能清单。"""
    if not skills:
        console.print(f"[{THEME['dim']}]没有发现技能（扫描了 ~/.agents/skills 和项目 .agents/skills）[/{THEME['dim']}]")
        return
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style=f"bold {THEME['accent']}", no_wrap=True)
    table.add_column(style=THEME["dim"])
    for s in skills:
        table.add_row(s["name"], s["description"] or "(无描述)")
    console.print(Panel(
        table,
        title=f"[bold]可用技能 · {len(skills)} 个[/bold]",
        title_align="left",
        border_style=THEME["dim"],
        padding=(1, 1),
    ))


def print_sessions(sessions: list, current_id: str):
    """打印历史会话清单（/sessions 命令用）。

    参数：
      sessions   — list_sessions() 返回的列表（新的在前）
      current_id — 当前会话的 id（标注一个 ← 当前）
    """
    if not sessions:
        console.print(f"[{THEME['dim']}]还没有历史会话（聊过天才会生成）[/{THEME['dim']}]")
        return
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style=f"bold {THEME['accent']}", no_wrap=True)  # id
    table.add_column(style=THEME["dim"], no_wrap=True)               # 时间
    table.add_column(style="default")                                 # 标题
    for s in sessions:
        when = datetime.fromtimestamp(s["mtime"]).strftime("%m-%d %H:%M")
        mark = " ← 当前" if s["id"] == current_id else ""
        table.add_row(s["id"], when, s["title"] + mark)
    console.print(Panel(
        table,
        title=f"[bold]历史会话 · {len(sessions)} 个[/bold]",
        subtitle="[dim]/resume <id> 恢复当前 Profile 的会话[/dim]",
        title_align="left",
        border_style=THEME["dim"],
        padding=(1, 1),
    ))


def print_prompt_debug(messages: list):
    """显示当前会发给 LLM 的消息结构（/prompt 命令用）。"""
    total_tokens = get_token_count(messages)
    console.print(f"\n[{THEME['dim']}]共 {len(messages)} 条消息 · 约 {total_tokens} token[/{THEME['dim']}]")

    table = Table(show_header=True, box=None, padding=(0, 1))
    table.add_column("#", style=THEME["dim"], width=3)
    table.add_column("Role", style=f"bold {THEME['accent']}", width=10)
    table.add_column("字符", style=THEME["dim"], justify="right", width=6)
    table.add_column("Token", style=THEME["dim"], justify="right", width=6)
    table.add_column("内容预览", style="default")

    for i, msg in enumerate(messages):
        role = msg.get("role", "?")
        content = msg.get("content", "") or ""
        preview = content[:80].replace("\n", " ").replace("\r", "")
        if len(content) > 80:
            preview += "…"
        table.add_row(str(i), role, str(len(content)), str(get_token_count([msg])), Text(preview))

    console.print(table)

    # 标注固定前缀 vs 对话历史
    fixed_count = sum(1 for m in messages if m.get("role") == "system")
    if fixed_count > 0 and fixed_count < len(messages):
        console.print(f"[{THEME['dim']}]  ↑ 前 {fixed_count} 条 system = 固定前缀（Prompt Cache 友好）[/{THEME['dim']}]")
        console.print(f"[{THEME['dim']}]  ↓ 后 {len(messages) - fixed_count} 条 = 对话历史（每轮变化）[/{THEME['dim']}]")
    console.print()


# ============================================================
# 状态栏 / 分隔线 / Agent 回复面板
# ============================================================


def print_status_bar(llm, messages: list, token_budget: int):
    """显示本次用量和当前输入估算，保留服务端值的来源标记。"""

    parts = []

    # 首字延迟
    if llm.last_ttft_ms is not None:
        parts.append(f"首字 {llm.last_ttft_ms:.0f}ms")

    # token 消耗
    if llm.total_prompt_tokens or llm.total_completion_tokens:
        parts.append(f"↑{llm.total_prompt_tokens} ↓{llm.total_completion_tokens}")
    if not getattr(llm, "usage_complete", True):
        parts.append("用量报告不完整")
    if llm.last_prompt_tokens is not None:
        parts.append(f"上次输入 {llm.last_prompt_tokens}（服务端）")

    # 输入估算与预算分开列明。
    # 上次请求用量不等于当前上下文，回复与工具结果可能已经追加进来了。
    ctx_tokens = get_token_count(messages, llm.model, tools=llm.tools_schemas)
    if token_budget > 0:
        pct = int(ctx_tokens / token_budget * 100)
        parts.append(f"输入估算 {ctx_tokens:,}/{token_budget:,} ({pct}%)")

    if parts:
        console.print(Text("  " + " · ".join(parts), style=THEME["dim"]))


def ask_permission_confirm(request: dict) -> bool:
    """ASK 权限确认 UI：展示安全请求视图，问用户同不同意。

    参数：
      request — Harness 生成的权限摘要和脱敏详情

    返回：
      True  — 用户同意执行（直接回车也算同意）
      False — 用户输入 n 拒绝

    谁调用：cli.py 主循环碰到 permission_required 时。
    """
    import json

    # 参数渲染成好读的 JSON（中文不转义）
    try:
        args_str = json.dumps(request["details"], ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        args_str = str(request["details"])

    body = Text(request["summary"], style="bold")
    body.append("\n\n" + args_str, style=THEME["dim"])
    console.print()
    console.print(Panel(
        body,
        title=Text("需要确认", style=THEME["warning"]),
        title_align="left",
        border_style=THEME["warning"],
    ))
    console.print(f"[{THEME['warning']}]同意执行？回车同意，输入 n 拒绝[/{THEME['warning']}]")

    try:
        from auto_coding_machine.profiles.coding.cli_input import confirm_input
        choice = confirm_input("  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        # Ctrl+C / Ctrl+D 算拒绝，不中断会话
        console.print(f"[{THEME['dim']}]已拒绝[/{THEME['dim']}]")
        return False

    # 回车（空输入）或 y 都算同意；只有明确输入其它内容才拒绝
    return choice in ("", "y")


def agent_reply_panel(reply: str, style: str, *, theme=None):
    """同步和流式回答共用的轻量 Markdown 面板。"""
    theme = theme or THEME
    return Panel(
        Markdown(reply),
        title=Text("✦ Agent", style=f"bold {theme.get('accent', theme['ai'])}"),
        title_align="left",
        border_style=theme["ai"] if style == theme.get("success", "green") else style,
        padding=(0, 1),
    )


def print_agent_reply(reply: str, style: str):
    """把 Agent 的回复渲染成 Markdown 面板（非流式回退时用）。"""
    console.print()
    console.print(agent_reply_panel(reply, style))
    console.print()


def print_history_replay(history: list):
    """resume 后把之前的对话回放到终端（让用户看到聊到哪了）。

    只回放正文：用户说的话 + 助手的文字回复。
    工具调用只显示一行提示，工具返回结果直接跳过
    （那些内容又长又乱，回放出来反而淹没重点）。

    参数：
      history — 从 session 文件读回的消息列表
    """
    if not history:
        return
    console.print()
    console.print(f"[{THEME['dim']}]── 以下是之前的对话（已恢复）──[/{THEME['dim']}]")
    for msg in history:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "user":
            # 技能注入这种超长消息只显示首行提示，不刷屏
            first_line = content.split("\n")[0]
            if len(first_line) > 200:
                first_line = first_line[:200] + "…"
            line = Text("\n❯ ", style=f"bold {THEME['user']}")
            line.append(first_line, style="default")
            console.print(line)
        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                # 工具调用轮：只显示调了哪些工具，不展开参数
                names = ", ".join(tc.get("function", {}).get("name", "?") for tc in tool_calls)
                console.print(Text(f"  › 调用工具: {names}", style=THEME["dim"]))
            elif content:
                # 文字回复：用和实时对话一样的 Markdown 面板，但用暗色边框区分
                console.print(agent_reply_panel(content, THEME["ai"]))
        # tool 消息（工具返回结果）直接跳过
    console.print(f"\n[{THEME['dim']}]── 回放结束，接着聊 ──[/{THEME['dim']}]")


def turn_separator():
    """回合分隔线：让对话轮次之间有清晰的视觉边界。"""
    console.rule(style=THEME["dim"], characters="─")


# ============================================================
# 工具调用显示（pre_tool 参数摘要用）
# ============================================================


# 事件输出（Hook 回调注册，取代旧的 ConsoleEventSink）
# ============================================================


def register_cli_hooks(hooks: HookManager):
    """把终端输出注册为 Hook 回调。

    取代了旧的 ConsoleEventSink(EventSink) 子类。
    核心变化：不再是"一个对象实现一个接口"，而是"向 HookManager 注册多个回调"。

    注册的事件：
      pre_tool   — 显示工具请求和主要参数
      post_tool  — 显示执行耗时、拒绝或失败原因类别
      done       — 任务完成
      cancelled  — 任务被取消

    参数：
      hooks — HookManager 实例（由 cli.py 创建并传入）
    """

    def on_pre_tool(**kw):
        """用短状态行显示工具及脱敏后的主要参数。"""
        arguments = sanitize_public_value(kw.get("arguments") or {})
        target = next((arguments[key] for key in ("path", "command", "query", "name")
                       if key in arguments), None)
        line = Text("  › ", style=THEME["dim"], no_wrap=True, overflow="ellipsis")
        line.append(str(kw.get("tool_name", "?")), style=f"bold {THEME['tool']}")
        if target is not None:
            line.append("  " + str(target).replace("\n", " ").replace("\r", ""),
                        style=THEME["dim"])
        console.print(line)

    def on_post_tool(**kw):
        """成功显示耗时，拒绝或失败显示原因类别。"""
        error = kw.get("error", False)
        error_type = kw.get("error_type") or "unknown"
        duration_ms = kw.get("duration_ms") or 0
        tool_name = str(kw.get("tool_name", "tool"))
        if error:
            label = "拒绝" if error_type in ("permission", "hook_denied") else "失败"
            line = Text(f"  × {tool_name} · {label} · {error_type}", style=THEME["warning"]
                        if label == "拒绝" else THEME["error"])
        else:
            elapsed = f"{duration_ms}ms" if duration_ms < 1000 else f"{duration_ms / 1000:.1f}s"
            line = Text(f"  ✓ {tool_name}", style=THEME["success"])
            line.append(f" · {elapsed}", style=THEME["dim"])
        line.no_wrap = True
        line.overflow = "ellipsis"
        console.print(line)
    def on_done(**kw):
        """任务完成。"""
        console.print(f"  [{THEME['success']}]✓ 完成[/{THEME['success']}]")

    def on_cancelled(**kw):
        """任务取消。"""
        console.print(f"  [{THEME['warning']}]⚠ 取消[/{THEME['warning']}]")

    def on_compacted(**kw):
        """上下文被压缩时提醒用户：旧消息被收走了，不是模型失忆。"""
        dropped = kw.get("dropped", "?")
        kept = kw.get("kept", "?")
        console.print(
            f"  [{THEME['warning']}]⚠ 上下文已压缩：收起 {dropped} 条旧消息，保留 {kept} 条（旧内容已摘要）[/{THEME['warning']}]"
        )

    def on_compaction_fallback(**kw):
        """显示摘要摘录兜底或上下文超限强制重试提醒。

        两种情况共用一个警告事件，通过 kind 区分文案。
        """
        kind = kw.get("kind") or "summary_fallback"
        error = kw.get("error") or "未知原因"
        if kind == "context_overflow_retry":
            console.print(
                f"  [{THEME['warning']}]⚠ {error}[/{THEME['warning']}]"
            )
            return
        console.print(
            f"  [{THEME['warning']}]⚠ 摘要失败（{error}），已改用历史摘录；"
            f"细节可用 recall_history 找回[/{THEME['warning']}]"
        )

    def on_completion_rejected(**kw):
        """模型宣布完成但缺少验证证据（FR-30）。

        只显示一行简短状态，不显示第二个完整回答框——
        正式回答由 Gate 裁定后统一提交，用户每个任务只看到一份持久回答。
        """
        console.print(
            f"  [{THEME['warning']}]⏳ 正在验证修改…（最后一次修改后缺少验证证据）[/{THEME['warning']}]"
        )

    handlers = {
        "pre_tool": on_pre_tool,
        "post_tool": on_post_tool,
        "done": on_done,
        "cancelled": on_cancelled,
        "compacted": on_compacted,
        "compaction_fallback": on_compaction_fallback,
        "completion_rejected": on_completion_rejected,
    }

    def on_event(event):
        handler = handlers.get(event.name)
        if handler is not None:
            handler(**event.data)

    hooks.on_event(on_event)
