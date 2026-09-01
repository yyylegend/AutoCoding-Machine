"""AutoCoding Machine CLI：交互式终端。

【这文件是干什么的】
  Coding Agent 的终端入口，直接组装 Runtime 并运行多轮对话。

【怎么用】
  python -m src.profiles.coding.cli

【文件职责（拆分后）】
  本文件只管两件事：
    1. 上下文加载（指令文件 + 技能发现 + 组装 injections）
    2. REPL 主循环（读输入 → 分发命令 → 跑 MachineLoop → 显示结果）

  其它职责在：
    - cli_ui.py      → 所有终端渲染（Banner / Help / 状态栏 / 事件输出）
    - llm_adapter.py → LLM 适配器（基类 + 流式子类）
"""

from __future__ import annotations

import time
from pathlib import Path

from src.config.settings import settings
from src.engine import (
    MachineLoop,
    BudgetPolicy,
    CancellationToken,
    GuardManager,
    HookManager,
    PermissionManager,
    SessionStore,
    ToolResult,
    assemble,
    latest_session_id,
    list_sessions,
    new_session_id,
    open_session,
    repair_dangling_tool_results,
    sessions_dir_for,
)
from src.profiles.coding.context_setup import build_context_manager, resolve_token_budget
from src.profiles.coding.system_prompt import get_system_prompt
from src.profiles.coding.tools import CodingTools
from src.runtime.factory import create_coding_runtime
from src.profiles.coding.commands.cost import handle_cost
from src.profiles.coding.commands.help import handle_help
from src.profiles.coding.commands.memory import handle_memory
from src.profiles.coding.commands.prompt import handle_prompt
from src.profiles.coding.commands.sessions import handle_sessions
from src.profiles.coding.commands.skills import handle_skills
from src.profiles.coding.commands.status import handle_status
from src.profiles.coding.skills import discover_skills, load_skill_content
from src.profiles.coding.llm_adapter import StreamingAdapter
from src.profiles.coding.cli_input import create_main_session, main_input, confirm_input
from src.profiles.coding.cli_ui import (
    THEME,
    console,
    print_banner,
    print_help,
    print_skills,
    print_sessions,
    print_history_replay,
    print_prompt_debug,
    print_status_bar,
    print_agent_reply,
    ask_permission_confirm,
    turn_separator,
    register_cli_hooks,
)


# ============================================================
# 上下文加载：指令文件（全局+项目）+ 技能发现
# ============================================================

_PROJECT_INSTRUCTION_FILES = ["AGENTS.md", "CLAUDE.md"]  # 项目层候选文件名（主名在前，备胎在后）
_MAX_GLOBAL_CHARS = 5000   # 全局层上限（个人偏好 + 人格设定，给足空间）
_MAX_PROJECT_CHARS = 8000  # 项目层上限（团队约定，详细）

# 注：上下文 token 预算的计算已统一收到 context_setup.py（单一真相源）


def _truncate_at_section(text: str, limit: int) -> tuple:
    """把文本截断到 limit 字符内，尽量在章节标题处切。

    返回 (截断后的文本, 是否发生了截断)。
    """
    if len(text) <= limit:
        return text, False
    # 在 limit 之前找最后一个章节标题（# 或 ## 开头的行），从那里切
    cut = text.rfind("\n#", 0, limit)
    if cut > limit // 2:  # 至少保留一半内容，否则就硬切
        return text[:cut].rstrip() + "\n\n（后续章节已省略）", True
    return text[:limit], True


def _global_instruction_paths() -> list:
    """返回全局指令文件的候选路径（按优先级）。

    兼容两个主流约定：
      1. ~/.agents/AGENTS.md  — 新兴标准
      2. ~/.claude/CLAUDE.md  — Claude Code 的主流约定
    """
    home = Path.home()
    return [
        home / ".agents" / "AGENTS.md",
        home / ".claude" / "CLAUDE.md",
    ]


def _load_first_found(candidate_paths: list, cap: int):
    """在候选路径里找第一个存在的文件，截断后返回。

    返回：
      找到：{"path": Path, "content": str, "truncated": bool}
      没找到：None
    """
    for path in candidate_paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        content, truncated = _truncate_at_section(text, cap)
        return {"path": path, "content": content, "truncated": truncated}
    return None


def load_instructions(workspace: Path) -> dict:
    """加载全局 + 项目两层指令文件。

    规则：
      - 层内：主备二选一（AGENTS.md 优先，没有才用 CLAUDE.md）
      - 层间：全局 + 项目都加载，拼接（不是覆盖）

    返回：
      {"global": {...} 或 None, "project": {...} 或 None}
    """
    global_info = _load_first_found(_global_instruction_paths(), _MAX_GLOBAL_CHARS)
    project_paths = [workspace / name for name in _PROJECT_INSTRUCTION_FILES]
    project_info = _load_first_found(project_paths, _MAX_PROJECT_CHARS)
    return {"global": global_info, "project": project_info}


def build_injections(instructions: dict, skills: list) -> list:
    """把指令文件（全局+项目）+ 技能清单组装成 dynamic_injections。"""
    injections = []

    # ---- 指令文件：全局 + 项目两层拼接 ----
    parts = []
    src_paths = []
    g = instructions.get("global")
    p = instructions.get("project")
    if g:
        parts.append("【全局约定】\n" + g["content"])
        src_paths.append(str(g["path"]))
    if p:
        parts.append("【项目约定】\n" + p["content"])
        src_paths.append(str(p["path"]))

    if parts:
        header = "以下是项目约定（已截取关键部分）。如需完整细节，用 read_file 读取原文件：\n"
        header += "原文件路径：" + "、".join(src_paths) + "\n\n"
        injections.append({"role": "system", "content": header + "\n\n".join(parts)})

    # ---- 技能清单：只注入名字（极省 token）----
    if skills:
        names = ", ".join(s["name"] for s in skills)
        injections.append({"role": "system", "content": (
            f"你可以通过 search_skills 工具按关键词搜索技能（detail=\"full\" 可查看描述），"
            f"找到后用 load_skill 加载完整说明。当前共 {len(skills)} 个可用技能：\n{names}"
        )})
    return injections


# ============================================================
# REPL 主循环
# ============================================================


def run_cli(resume=None):
    """运行 CLI 主循环。

    参数：
      resume — 见 open_session() 的说明（None=新开 / ""=最近 / id=指定）
    """
    print_banner()

    # 初始化 Tokenizer（根据 .env 模型名，单一真相源在 context_setup.init_coding_tokenizer）
    from src.profiles.coding.context_setup import init_coding_tokenizer
    init_coding_tokenizer()

    # ---- Session：新开或恢复（对话历史的唯一真相源，见 ADR-0001）----
    workspace = Path.cwd()
    store, history, error = open_session(sessions_dir_for(workspace), resume)
    if error:
        console.print(f"[{THEME['error']}]{error}[/{THEME['error']}]")
        return

    tools = CodingTools(workspace, max_output_chars=2000)
    # 把 ToolManager 传给权限管理器：优先读各工具 @tool 声明的权限，
    # 不传的话新工具（memory / search_skills 等）会被硬编码白名单 DENY
    permission = PermissionManager(tool_manager=tools.get_manager())
    guard = GuardManager()

    # LLM 适配器（流式版，传 console 和 THEME 给它做终端渲染）
    llm = StreamingAdapter(tools.get_schemas(), console, THEME)

    # 上下文管理器：统一从 context_setup 构造（只看 token 预算 + 摘要，见 ADR-0003）
    token_budget = resolve_token_budget()
    context_mgr = build_context_manager(token_budget=token_budget)
    budget = BudgetPolicy(max_turns=settings.CODING_MAX_TURNS)
    hooks = HookManager()
    register_cli_hooks(hooks)

    # 指令文件 + 技能发现，作为 Runtime 的基础注入快照。
    instructions = load_instructions(workspace)
    skills = discover_skills(workspace)
    base_injections = build_injections(instructions, skills)

    # Plan Mode 状态（运行时切换）
    plan_mode = False
    plan_injection = None   # Plan Mode 注入的 system 消息（/plan 时加入，/exit 时移除）

    # Runtime 统一组装记忆、工具、权限、上下文和 MachineLoop。
    runtime = create_coding_runtime(
        workspace=workspace,
        model_fn=llm.call,
        tools=tools,
        hooks=hooks,
        session_store=store,
        context_manager=context_mgr,
        permission=permission,
        guard=guard,
        budget=budget,
        base_injections=base_injections,
    )
    system_prompt = runtime.system_prompt
    injections = runtime.registry.build_injections()
    # 第一轮只迁移 /status，其他命令暂时保持原实现。
    runtime.registry.register_command("/status", "显示当前会话状态", handle_status)
    runtime.registry.register_command("/cost", "显示 Token 消耗", handle_cost)
    runtime.registry.register_command("/help", "显示帮助", handle_help)
    runtime.registry.register_command("/skills", "列出可用技能", handle_skills)
    runtime.registry.register_command("/memory", "查看长期记忆", handle_memory)
    runtime.registry.register_command("/sessions", "列出历史会话", handle_sessions)
    runtime.registry.register_command("/prompt", "查看当前消息结构", handle_prompt)

    def build_messages(current_history):
        # Plan 注入只作为临时尾部内容，不改变稳定 system prompt。
        extra = [plan_injection] if plan_injection is not None else None
        return runtime.build_messages(current_history, extra_injections=extra)

    # 组装初始消息（history 来自 open_session：新会话是空，resume 是读回的历史）。
    messages = build_messages(history)
    memory_injection = next((item for item in injections if "MEMORY" in item.get("content", "")), None)

    # 启动信息
    console.print(f"[{THEME['success']}]✓ Agent 已就绪[/{THEME['success']}]  "
                  f"[{THEME['dim']}]模型: {llm.model}[/{THEME['dim']}]")
    console.print(f"[{THEME['dim']}]✓ 上下文预算: {token_budget} tokens[/{THEME['dim']}]")
    if history:
        console.print(f"[{THEME['success']}]✓ 已恢复会话 {store.session_id}（{len(history)} 条历史消息）[/{THEME['success']}]")
    else:
        console.print(f"[{THEME['dim']}]✓ 新会话: {store.session_id}[/{THEME['dim']}]")
    if instructions["global"]:
        g = instructions["global"]
        console.print(f"[{THEME['dim']}]✓ 已加载全局约定: {g['path']}"
                      f"{'（已截断）' if g['truncated'] else ''}[/{THEME['dim']}]")
    if instructions["project"]:
        p = instructions["project"]
        console.print(f"[{THEME['dim']}]✓ 已加载项目约定: {p['path'].name}"
                      f"{'（已截断）' if p['truncated'] else ''}[/{THEME['dim']}]")
    if skills:
        console.print(f"[{THEME['dim']}]✓ 发现 {len(skills)} 个技能（/skills 查看）[/{THEME['dim']}]")
    if memory_injection:
        console.print(f"[{THEME['dim']}]✓ 已加载长期记忆（.autocoding/MEMORY.md + ~/.autocoding/USER.md）[/{THEME['dim']}]")
    print_help()

    # resume 时把之前的对话回放到屏幕上（模型能看到，人也得能看到）
    print_history_replay(history)

    # prompt_toolkit 会话（上下键历史 + 斜杠命令菜单 + 参数补全 + Alt+Enter 多行）
    prompt_session = create_main_session(
        workspace,
        skills=skills,
        get_sessions=lambda: list_sessions(sessions_dir_for(workspace)),
    )

    # 进程内的对话视图状态（不落盘，不违反 ADR-0002）
    # base_view  = 压缩产物（摘要 + 保留的近期消息）；/clear 后为空
    # base_count = JSONL 里第几条之后是“新消息”（视图起点）
    # 每轮重建：history = base_view + store.load()[base_count:]
    base_view = []
    base_count = 0

    # Ctrl+C 计数
    interrupted_once = False

    # REPL 循环
    while True:
        try:
            user_input = main_input(prompt_session, plan_mode)
            user_input = user_input.strip()
            interrupted_once = False

            if not user_input:
                continue

            # ---- 命令分发 ----
            if user_input in ("/quit", "/exit"):
                if plan_mode:
                    # Plan Mode 下 /exit 只退出计划模式，不退出程序
                    plan_mode = False
                    permission.plan_mode = False
                    # 移除注入的 Plan Mode 指令（不动 system_prompt 本体，保持缓存前缀稳定）
                    if plan_injection is not None:
                        plan_injection = None
                    console.print(f"[{THEME['success']}]✓ 已退出 Plan Mode，恢复正常模式[/{THEME['success']}]\n")
                    continue
                console.print(f"\n[{THEME['dim']}]再见！[/{THEME['dim']}]\n")
                break

            # ---- /plan：进入 Plan Mode（只读 + 结构化计划）----
            if user_input == "/plan":
                if plan_mode:
                    console.print(f"[{THEME['dim']}]已经在 Plan Mode 了[/{THEME['dim']}]")
                    continue
                plan_mode = True
                # Plan Mode 指令作为独立 system 注入（不动 system_prompt 本体，缓存前缀稳定）
                from src.profiles.coding.plan_mode import get_plan_mode_injection
                plan_injection = {"role": "system", "content": get_plan_mode_injection()}
                permission.plan_mode = True
                console.print(f"\n  [{THEME['accent']}]═══ PLAN MODE（只读模式）═══[/{THEME['accent']}]")
                console.print(f"  [{THEME['accent']}]只能调用只读工具，产出结构化计划[/{THEME['accent']}]")
                console.print(f"  [{THEME['accent']}]输入 /exit 退出 Plan Mode[/{THEME['accent']}]\n")
                continue

            # ---- 已迁移命令：交给 Runtime Registry ----
            if user_input == "/help":
                runtime.registry.run_command("/help", {})
                continue

            # ---- 已迁移命令：交给 Runtime Registry ----
            if user_input == "/skills":
                runtime.registry.run_command("/skills", {"skills": skills})
                continue

            if user_input == "/sessions":
                runtime.registry.run_command("/sessions", {
                    "sessions": list_sessions(sessions_dir_for(workspace)),
                    "current_id": store.session_id,
                })
                continue

            # ---- /resume：会话内切换（不用退出重启）----
            if user_input == "/resume" or user_input.startswith("/resume "):
                target = user_input[len("/resume"):].strip()
                if not target:
                    # 不带 id：先列出会话，教一下用法
                    print_sessions(list_sessions(sessions_dir_for(workspace)), store.session_id)
                    console.print(f"[{THEME['dim']}]用法：/resume <id>（输入时 Tab 可补全 id）[/{THEME['dim']}]\n")
                    continue
                new_store, new_history, switch_error = open_session(sessions_dir_for(workspace), target)
                if switch_error:
                    console.print(f"[{THEME['error']}]{switch_error}[/{THEME['error']}]\n")
                    continue
                store = new_store
                runtime.set_session_store(store)
                history = new_history
                base_view = []   # 新会话没有压缩产物，视图从头开始
                base_count = 0
                messages = build_messages(history)
                llm.reset_metrics()
                console.print(f"[{THEME['success']}]✓ 已切换到会话 {store.session_id}（{len(history)} 条历史消息）[/{THEME['success']}]")
                print_history_replay(history)
                continue

            if user_input == "/prompt":
                current_msgs = assemble(system_prompt, history, dynamic_injections=injections)
                runtime.registry.run_command("/prompt", {"messages": current_msgs})
                continue

            if user_input.startswith("/skill "):
                skill_name = user_input[len("/skill "):].strip()
                content = load_skill_content(skills, skill_name)
                if content is None:
                    console.print(f"[{THEME['error']}]没找到技能: {skill_name}（/skills 查看可用清单）[/{THEME['error']}]")
                else:
                    skill_msg = {
                        "role": "user",
                        "content": f"[技能注入] 以下是技能 {skill_name} 的使用说明，后续任务请参考：\n\n{content}",
                    }
                    history.append(skill_msg)
                    store.append(skill_msg)  # 技能注入也是历史的一部分，resume 后不能丢
                    console.print(f"[{THEME['success']}]✓ 已注入技能: {skill_name}[/{THEME['success']}]")
                continue

            # ---- 已迁移命令：交给 Runtime Registry ----
            if user_input == "/status":
                runtime.registry.run_command("/status", {
                    "messages": messages,
                    "token_budget": token_budget,
                    "console": console,
                    "theme": THEME,
                    "llm": llm,
                    "store": store,
                    "history": history,
                    "tools": tools,
                    "plan_mode": plan_mode,
                })
                continue

            # ---- 已迁移命令：交给 Runtime Registry ----
            if user_input == "/cost":
                runtime.registry.run_command("/cost", {
                    "console": console,
                    "theme": THEME,
                    "llm": llm,
                })
                continue

            # ---- 已迁移命令：交给 Runtime Registry ----
            if user_input == "/memory":
                runtime.registry.run_command("/memory", {"workspace": workspace})
                continue

            # ---- /clear：清屏 + 重置对话（JSONL 保留）----
            if user_input == "/clear":
                import os
                os.system("cls" if os.name == "nt" else "clear")
                # 视图起点设为当前 JSONL 末尾：之前的消息全不看了
                base_view = []
                base_count = len(store.load())
                messages = build_messages([])
                console.print(f"[{THEME['dim']}]✓ 屏幕已清，对话已重置（JSONL 历史保留，recall_history 仍可搜）[/{THEME['dim']}]")
                continue

            # ---- /compact：手动压缩（带确认）----
            if user_input == "/compact":
                from src.engine.context_manager import count_tokens
                ctx_tokens = count_tokens(messages)
                pct = int(ctx_tokens / token_budget * 100) if token_budget > 0 else 0
                console.print(f"[{THEME['dim']}]  当前上下文: {ctx_tokens}/{token_budget} ({pct}%)[/{THEME['dim']}]")
                try:
                    confirm = confirm_input("  确定压缩？回车确认，输入 n 取消 > ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    console.print(f"[{THEME['dim']}]  已取消[/{THEME['dim']}]")
                    continue
                if confirm in ("", "y"):
                    # 对当前视图压缩，结果存进 base_view，跨轮生效
                    before_count = len(history)
                    compacted = context_mgr.maybe_compact(history, force=True)
                    if len(compacted) < before_count:
                        base_view = compacted
                        base_count = len(store.load())
                        history = base_view[:]  # 压缩后还没有新消息，视图就是全部
                        messages = build_messages(history)
                        new_tokens = count_tokens(messages)
                        console.print(f"[{THEME['success']}]  ✓ 已压缩：{before_count} → {len(base_view)} 条（约 {new_tokens} token）[/{THEME['success']}]")
                    else:
                        console.print(f"[{THEME['dim']}]  当前无需压缩（对话太短）[/{THEME['dim']}]")
                else:
                    console.print(f"[{THEME['dim']}]  已取消[/{THEME['dim']}]")
                continue

            # ---- 正常对话：追加用户消息（同步落盘）→ 跑 MachineLoop ----
            user_msg = {"role": "user", "content": user_input}
            store.append(user_msg)
            # 重建视图：压缩产物 + JSONL 里视图起点之后的新消息
            full = store.load()
            history = base_view + full[base_count:]
            messages = build_messages(history)

            cancel = CancellationToken()
            # Runtime 已在启动时统一组装，循环本身可以跨轮复用。
            loop = runtime.loop

            llm.reset_metrics()
            # Ctrl+C 在这一整段里的语义 = 取消当前任务（不退出 CLI）。
            # KeyboardInterrupt 可能从 loop.run（LLM 请求 / 工具执行）或
            # 权限确认后的继续执行中抛出来，统一在这里接住做平滑取消。
            try:
                result = loop.run(messages, cancel)

                # ---- ASK 权限确认：同意就真执行工具，结果回填后继续跑循环 ----
                # 权限恢复：approve → tools.execute；deny → 构造拒绝结果。
                # 用 while 而不是 if：一轮里可能连续碰到多个 ASK 工具。
                while result["status"] == "permission_required":
                    tc = result["pending_tool_call"]
                    approved = ask_permission_confirm(tc.name, tc.arguments)

                    if approved:
                        # 用户同意 → 真正执行工具（这才是关键，不能只塞一句假话）
                        t0 = time.time()
                        tool_result = tools.execute(tc)
                        duration_ms = int((time.time() - t0) * 1000)
                    else:
                        # 用户拒绝 → 造一条拒绝结果，模型会看到并换思路
                        tool_result = ToolResult(
                            tool_call_id=tc.id,
                            content="用户拒绝了这次操作",
                            error=True,
                            error_type="permission",
                            retryable=False,
                        )
                        duration_ms = 0

                    # post_tool Hook 照常触发（和 Loop 内自动执行路径一致，
                    # 终端状态行/事件监听都靠它，走确认流也不能丢事件）
                    hooks.fire("post_tool",
                        tool_name=tc.name, tool_call_id=tc.id,
                        error=tool_result.error, error_type=tool_result.error_type,
                        result_content=tool_result.content,
                        result_metadata=tool_result.metadata,
                        duration_ms=duration_ms, turn=result.get("turn", 0))

                    # 结果回填消息 + 落盘（不落盘的话 JSONL 里 tool_calls 断配对，resume 会坏）
                    messages = result["messages"]
                    result_msg = tool_result.to_message()
                    messages.append(result_msg)
                    store.append(result_msg)

                    # 带着结果继续跑循环，让模型接着干活
                    result = loop.run(messages, cancel)

            except KeyboardInterrupt:
                # 平滑取消：任务停掉，会话保留
                # 1) 标记取消（Loop 若还活着会走 cancelled 退出）
                cancel.cancel()
                # 2) 修复悬空的 tool_calls：assistant 落了盘但工具结果没写，
                #    不补的话 resume 会因消息断配对而报错
                repaired = repair_dangling_tool_results(store)
                # 3) 从 JSONL 重建视图（单一真相源，见 ADR-0001）
                full = store.load()
                history = base_view + full[base_count:]
                messages = build_messages(history)
                console.print(f"\n  [{THEME['warning']}]⚠ 任务已取消（会话保留，可继续聊；/quit 退出）[/{THEME['warning']}]\n")
                if repaired:
                    console.print(f"  [{THEME['dim']}]已补齐 {repaired} 条被中断的工具结果（续聊/resume 不受影响）[/{THEME['dim']}]")
                turn_separator()
                continue

            # 本轮新产生的消息（助手回复/工具结果）已由 loop 逐条写进 JSONL。
            # 从文件重建视图：保证内存和文件一致（单一真相源）。
            full = store.load()
            history = base_view + full[base_count:]

            # ---- 输出结果 ----
            if result["status"] == "success":
                if not llm.last_streamed:
                    print_agent_reply(result.get("reply", ""), THEME["success"])
                if plan_mode:
                    console.print(f"  [{THEME['accent']}]📋 计划已生成，输入 /exit 退出 Plan Mode 开始执行[/{THEME['accent']}]")
                print_status_bar(llm, messages, token_budget)
            elif result["status"] == "need_input":
                if not llm.last_streamed:
                    print_agent_reply(result.get("reply", ""), THEME["warning"])
                print_status_bar(llm, messages, token_budget)
            elif result["status"] == "cancelled":
                console.print(f"\n[{THEME['warning']}]任务已取消[/{THEME['warning']}]\n")
                break
            else:
                error = result.get("error", "unknown")
                console.print(f"\n[{THEME['error']}]✗ 失败：{error}[/{THEME['error']}]\n")

            turn_separator()

        except KeyboardInterrupt:
            if interrupted_once:
                console.print(f"\n[{THEME['dim']}]再见！[/{THEME['dim']}]\n")
                break
            interrupted_once = True
            console.print(f"\n\n[{THEME['warning']}]再按一次 Ctrl+C 退出，或输入 /quit[/{THEME['warning']}]\n")
            continue
        except EOFError:
            console.print(f"\n[{THEME['dim']}]再见！[/{THEME['dim']}]\n")
            break
        except Exception as exc:
            import requests as _requests
            if isinstance(exc, _requests.HTTPError):
                console.print(f"\n[{THEME['error']}]✗ LLM 请求失败：{str(exc)[:300]}[/{THEME['error']}]")
                console.print(f"[{THEME['dim']}]提示：若报'包含图片内容'，通常是中转商误判了代码里的字面量[/{THEME['dim']}]\n")
            else:
                console.print(f"\n[{THEME['error']}]错误: {exc}[/{THEME['error']}]\n")
                console.print_exception(show_locals=False)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Coding Agent CLI")
    # nargs="?" 让 --resume 可以不带值：
    #   不写 --resume        → resume=None（新开会话）
    #   只写 --resume        → resume=""（恢复最近一次）
    #   写 --resume <id>     → resume="<id>"（恢复指定会话）
    parser.add_argument(
        "--resume", nargs="?", const="", default=None, metavar="SESSION_ID",
        help="恢复会话：不带值恢复最近一次，带值恢复指定 id（/sessions 可查）",
    )
    args = parser.parse_args()
    run_cli(resume=args.resume)
