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
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from src.engine.hook_manager import HookManager


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
# 对话区分：user 暖色 / ai 冷色，一眼区分谁在说话
THEME = {
    "primary": "cyan",           # 主色：标题、边框
    "accent": "bright_cyan",     # 强调：UI 元素
    "success": "green",          # 成功
    "warning": "yellow",         # 警告 / 需要输入
    "error": "red",              # 错误
    "dim": "grey50",             # 次要信息
    "tool": "blue",              # 工具调用
    "user": "bright_magenta",    # 用户消息：暖色
    "ai": "cyan",                # AI 消息：冷色
}


# ============================================================
# Banner（欢迎界面）
# ============================================================


def print_banner():
    """打印欢迎 Banner。

    两套方案自动切换：
      - 宽终端（>= 86 列）：大字 ASCII Logo + 逐行渐变色（炫酷版）
      - 窄终端：紧凑 Panel（不破版）

    Logo 是开发时用 pyfiglet（ansi_shadow 字体）生成后贴进来的字符串常量，
    运行时不依赖 pyfiglet，也不会有手写 ASCII 画对不齐的问题。
    """
    if console.width >= 86:
        _print_big_banner()
    else:
        _print_compact_banner()


# 大字 Logo（pyfiglet ansi_shadow 字体生成，最宽 82 列）
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

# 逐行渐变色：青 → 蓝 → 紫 → 品红（12 行 Logo 对应 12 个颜色）
_LOGO_COLORS = [
    "#00e5ff", "#00c8ff", "#00aaff", "#3d8bff", "#6a6aff", "#8a4fff",
    "#9a3fef", "#aa33d9", "#c02bbf", "#d424a4", "#e91e8c", "#ff1477",
]


def _print_big_banner():
    """宽终端：大字 Logo + 渐变色 + 信息行。"""
    console.print()
    for line, color in zip(_LOGO_LINES, _LOGO_COLORS):
        # no_wrap：临界宽度下宁可裁剪也不折行（折行会把 Logo 彻底打乱）
        console.print(Text(line, style=f"bold {color}", no_wrap=True), justify="center")

    subtitle = Text()
    subtitle.append("⚡ ", style="yellow")
    subtitle.append("Coding Agent · CLI Demo · Phase 2.5", style=f"bold {THEME['primary']}")
    console.print()
    console.print(subtitle, justify="center")

    info = Text()
    info.append("Workspace ", style=THEME["dim"])
    info.append(str(Path.cwd()), style="default")
    info.append("   Commands ", style=THEME["dim"])
    info.append("/help /quit", style=f"bold {THEME['accent']}")
    console.print(info, justify="center")
    console.print()


def _print_compact_banner():
    """窄终端：紧凑 Panel（宽度自适应，不破版）。"""
    title = Text()
    title.append("▐ ", style=THEME["primary"])
    title.append("AutoCoding Machine", style=f"bold {THEME['primary']}")
    title.append("  Coding Agent · CLI Demo · Phase 2.5", style=THEME["dim"])

    body = Text()
    body.append("Workspace  ", style=THEME["dim"])
    body.append(str(Path.cwd()), style="default")
    body.append("\nCommands   ", style=THEME["dim"])
    body.append("/help", style=f"bold {THEME['accent']}")
    body.append("  ", style="default")
    body.append("/quit", style=f"bold {THEME['accent']}")

    console.print()
    console.print(Panel(
        body,
        title=title,
        title_align="left",
        border_style=THEME["primary"],
        padding=(1, 2),
    ))
    console.print()


# ============================================================
# Help / 技能清单 / 调试
# ============================================================


def print_help():
    """打印帮助（用 Table 排版，对齐更整齐）。"""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style=f"bold {THEME['accent']}", no_wrap=True)
    table.add_column(style="default")

    table.add_row("/plan", "进入 Plan Mode（只读，产出结构化计划；/exit 退出）")
    table.add_row("/help", "显示帮助")
    table.add_row("/status", "当前状态（模式/模型/会话/token/上下文占比）")
    table.add_row("/cost", "本次会话 token 消耗")
    table.add_row("/memory", "查看长期记忆（MEMORY.md + USER.md）")
    table.add_row("/clear", "清屏 + 重置对话（JSONL 保留）")
    table.add_row("/compact", "手动压缩上下文（带确认）")
    table.add_row("/skills", "列出可用技能")
    table.add_row("/skill <名字>", "把指定技能的内容注入对话（名字可 Tab 补全）")
    table.add_row("/sessions", "列出历史会话（启动时用 --resume [id] 恢复）")
    table.add_row("/resume <id>", "切换到指定会话（id 可 Tab 补全；不带 id 则列出）")
    table.add_row("/prompt", "查看当前发给 LLM 的消息结构")
    table.add_row("/quit", "退出（/exit 同义）")
    table.add_row("", "")
    table.add_row("[dim]示例[/dim]", "[green]读一下 src/engine/contracts.py[/green]")
    table.add_row("", "[green]搜索所有包含 ToolCall 的文件[/green]")
    table.add_row("", "[green]列出 src/profiles 目录[/green]")

    console.print(Panel(
        table,
        title="[bold]命令与示例[/bold]",
        title_align="left",
        border_style=THEME["dim"],
        padding=(1, 1),
    ))


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
        subtitle="[dim]恢复：退出后用 --resume <id> 重新启动[/dim]",
        title_align="left",
        border_style=THEME["dim"],
        padding=(1, 1),
    ))


def print_prompt_debug(messages: list):
    """显示当前会发给 LLM 的消息结构（/prompt 命令用）。"""
    from src.engine.context_manager import count_tokens

    total_tokens = count_tokens(messages)
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
        table.add_row(str(i), role, str(len(content)), str(count_tokens([msg])), preview)

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
    """打印状态栏：首字延迟 + token 消耗 + 上下文压力条。"""
    from src.engine.context_manager import count_tokens

    parts = []

    # 首字延迟
    if llm.last_ttft_ms is not None:
        parts.append(f"⏱ 首字 {llm.last_ttft_ms:.0f}ms")

    # token 消耗
    if llm.total_prompt_tokens or llm.total_completion_tokens:
        parts.append(f"↑{llm.total_prompt_tokens} ↓{llm.total_completion_tokens}")

    # 上下文压力（进度条 + 数值）
    ctx_tokens = llm.last_prompt_tokens or count_tokens(messages)
    if token_budget > 0:
        pct = min(int(ctx_tokens / token_budget * 100), 100)
        filled = int(pct / 5)  # 20 格进度条
        bar = "█" * filled + "░" * (20 - filled)
        parts.append(f"ctx {bar} {ctx_tokens}/{token_budget} ({pct}%)")

    if parts:
        console.print(f"[{THEME['dim']}]  {' · '.join(parts)}[/{THEME['dim']}]")


def ask_permission_confirm(tool_name: str, arguments: dict) -> bool:
    """ASK 权限确认 UI：展示工具名和参数，问用户同不同意。

    参数：
      tool_name — 待确认的工具名（如 run_bash）
      arguments — 工具参数字典（展示给用户看清楚要干什么）

    返回：
      True  — 用户同意执行（直接回车也算同意）
      False — 用户输入 n 拒绝

    谁调用：cli.py 主循环碰到 permission_required 时。
    """
    import json

    # 参数渲染成好读的 JSON（中文不转义）
    try:
        args_str = json.dumps(arguments, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        args_str = str(arguments)

    console.print()
    console.print(Panel(
        f"[bold]{tool_name}[/bold]\n\n{args_str}",
        title=f"[{THEME['warning']}]⚠ 需要确认[/{THEME['warning']}]",
        border_style=THEME["warning"],
    ))
    console.print(f"[{THEME['warning']}]同意执行？回车同意，输入 n 拒绝[/{THEME['warning']}]")

    try:
        from src.profiles.coding.cli_input import confirm_input
        choice = confirm_input("  > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        # Ctrl+C / Ctrl+D 算拒绝，不中断会话
        console.print(f"[{THEME['dim']}]已拒绝[/{THEME['dim']}]")
        return False

    # 回车（空输入）或 y 都算同意；只有明确输入其它内容才拒绝
    return choice in ("", "y")


def print_agent_reply(reply: str, style: str):
    """把 Agent 的回复渲染成 Markdown 面板（非流式回退时用）。"""
    console.print()
    console.print(Panel(
        Markdown(reply),
        title="[bold]Agent[/bold]",
        title_align="left",
        border_style=style,
        padding=(1, 2),
    ))
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
            console.print(f"\n[bold {THEME['user']}]You ›[/bold {THEME['user']}] {first_line}")
        elif role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                # 工具调用轮：只显示调了哪些工具，不展开参数
                names = ", ".join(tc.get("function", {}).get("name", "?") for tc in tool_calls)
                console.print(f"[{THEME['dim']}]  ⏺ 调用工具: {names}[/{THEME['dim']}]")
            elif content:
                # 文字回复：用和实时对话一样的 Markdown 面板，但用暗色边框区分
                console.print(Panel(
                    Markdown(content),
                    title="[bold]Agent[/bold]",
                    title_align="left",
                    border_style=THEME["dim"],
                    padding=(0, 2),
                ))
        # tool 消息（工具返回结果）直接跳过
    console.print(f"\n[{THEME['dim']}]── 回放结束，接着聊 ──[/{THEME['dim']}]")


def turn_separator():
    """回合分隔线：让对话轮次之间有清晰的视觉边界。"""
    console.print(f"[{THEME['dim']}]◇ {'─' * max(console.width - 4, 10)} ◇[/{THEME['dim']}]")


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
      pre_tool   — 工具开始执行，卡片显示工具名 + 参数
      post_tool  — 工具执行完，打印一行结果状态（✓ 耗时/预览 或 ✗ 错误类型）
      done       — 任务完成
      cancelled  — 任务被取消

    参数：
      hooks — HookManager 实例（由 cli.py 创建并传入）
    """

    def on_pre_tool(**kw):
        """工具开始执行：卡片显示工具名 + 参数（rich Panel）。

        格式：
          ┌─ ⏺ read_file ────────────────┐
          │ path: src/engine/contracts.py  │
          │ max_chars: 2000                │
          └────────────────────────────────┘
        """
        tool_name = kw.get("tool_name", "?")
        arguments = kw.get("arguments") or {}

        # 参数渲染成一行一个 key: value（比一行摘要好读）
        body_lines = []
        for key, value in arguments.items():
            text = str(value).replace("\n", " ").replace("\r", "")
            if len(text) > 60:
                text = text[:60] + "…"
            body_lines.append(f"{key}: {text}")
        body = "\n".join(body_lines) if body_lines else "(无参数)"

        console.print(Panel(
            body,
            title=f"⏺ {tool_name}",
            title_align="left",
            border_style=THEME["tool"],
            padding=(0, 1),
            expand=False,
        ))

    def on_post_tool(**kw):
        """工具执行完：结果状态行（对齐在卡片下方）。

        成功：✓ 耗时 · 结果首行预览
        失败：✗ error_type
        """
        error = kw.get("error", False)
        error_type = kw.get("error_type", "ok")
        content = kw.get("result_content") or ""
        duration_ms = kw.get("duration_ms") or 0
        if error:
            console.print(f"  [{THEME['error']}]✗ {error_type}[/{THEME['error']}]")
        else:
            # 结果预览：只取第一行，太长截断（完整内容模型会看到，人扫一眼就够）
            first_line = content.split("\n", 1)[0].strip()
            if len(first_line) > 60:
                first_line = first_line[:60] + "…"
            preview = f" · {first_line}" if first_line else ""
            console.print(
                f"  [{THEME['success']}]✓ {duration_ms / 1000:.1f}s{preview}[/{THEME['success']}]"
            )
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

    hooks.on("pre_tool", on_pre_tool)
    hooks.on("post_tool", on_post_tool)
    hooks.on("done", on_done)
    hooks.on("cancelled", on_cancelled)
    hooks.on("compacted", on_compacted)
    hooks.on("compaction_fallback", on_compaction_fallback)
