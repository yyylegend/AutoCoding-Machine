"""Coding Runtime 工厂。

CLI 和其他调用方都通过这个 seam 获取公共组件。
"""

from dataclasses import dataclass
from pathlib import Path

from auto_coding_machine.common.logger import get_logger

from auto_coding_machine.engine import (
    BudgetPolicy,
    GuardManager,
    HookManager,
    MachineLoop,
    PermissionManager,
)
from auto_coding_machine.engine.events import public_event
from auto_coding_machine.engine.session_store import SessionStore, open_session
from auto_coding_machine.config.settings import settings
from auto_coding_machine.profiles.coding.completion_gate import CompletionGate
from auto_coding_machine.runtime.context_selector import ContextSelector
from auto_coding_machine.runtime.context import build_context_manager, profile_token_budget, resolve_context_info
from auto_coding_machine.engine.request_view import RequestView
from auto_coding_machine.memory.recall import MemoryRecallSelector
from auto_coding_machine.profiles.coding.sandbox import WorkspaceSandbox
from auto_coding_machine.profiles.coding.system_prompt import get_system_prompt
from auto_coding_machine.profiles.coding.profile import CodingProfile, MarkdownMemoryExtension
from auto_coding_machine.runtime.registry import RuntimeContext, RuntimeRegistry
from auto_coding_machine.runtime.runtime import AgentRuntime
from auto_coding_machine.profiles.config import Profile
from auto_coding_machine.runtime.tools import ProfileTools
from auto_coding_machine.runtime.trace import RunTrace
from auto_coding_machine.runtime.prompts import load_instructions, build_injections
from auto_coding_machine.runtime.status import AgentStatusBar

event_logger = get_logger("harness.events")


def _public_event_observer(callback):
    def observe(event):
        try:
            callback(public_event(event))
        except Exception as exc:
            event_logger.warning(
                "公共事件观察回调异常（已忽略）：event=%s error_type=%s",
                event.name,
                type(exc).__name__,
            )

    return observe


@dataclass(frozen=True)
class RuntimeComponents:
    """一组必须共同使用的 Runtime 组件。"""

    tools: object
    permission: object
    guard: object
    context_manager: object
    context_selector: object
    request_view: object
    completion_gate: object
    status_bar: object
    hooks: object
    budget: object
    context_info: dict | None
    token_budget: int | None
    memory_selector: object = None
    memory_writer: object = None
    memory_scope: object = None


def build_runtime_components(
    workspace,
    profile=None,
    *,
    tools=None,
    hooks=None,
    session_store=None,
    context_manager=None,
    context_selector=None,
    completion_gate=None,
    permission=None,
    guard=None,
    budget=None,
    auto_approve=False,
    status_bar=None,
    context_info=None,
    token_budget=None,
    memory_provider=None,
    memory_writer=None,
    memory_scope=None,
):
    """创建一组相互匹配的 Runtime 组件，供入口和 Factory 共享。"""
    profile = profile or Profile()
    if tools is None:
        tools = ProfileTools(workspace, profile)
    if hooks is None:
        hooks = HookManager()
    if permission is None:
        permission = PermissionManager(
            tool_manager=tools.get_manager(),
            auto_approve=auto_approve,
        )
    if guard is None:
        guard = GuardManager()

    if context_manager is None:
        context_info = context_info or resolve_context_info(profile.model)
        token_budget = (
            token_budget
            if token_budget is not None
            else profile_token_budget(profile, context_info=context_info)
        )
        context_manager = build_context_manager(
            token_budget=token_budget,
            model=profile.model,
            tools=tools.get_schemas(),
        )
    else:
        context_info = context_info or {"window": None, "source": "provided"}
        token_budget = getattr(context_manager, "max_tokens", None)

    # 自定义 Store 的历史来源未知，不自动扫描本地 JSONL。
    if context_selector is None and (
        session_store is None or isinstance(session_store, SessionStore)
    ):
        current_session_id = getattr(session_store, "session_id", None)
        context_selector = ContextSelector(
            workspace=workspace,
            current_session_id=current_session_id,
            sessions_dir=(
                session_store.path.parent if isinstance(session_store, SessionStore)
                else profile.state_dir(workspace) / "sessions"
            ),
        )
    verify_on_stop = (
        settings.CODING_VERIFY_ON_STOP
        if settings.CODING_VERIFY_ON_STOP is not None
        else profile.verify_changes
    )
    if completion_gate is None and profile.kind == "coding" and verify_on_stop:
        # 完成证据门是 Coding 专属策略，放在组件组装 seam 内统一创建。
        sandbox = getattr(tools, "sandbox", None)
        if not isinstance(sandbox, WorkspaceSandbox):
            sandbox = WorkspaceSandbox(workspace)
        completion_gate = CompletionGate(sandbox)
    if status_bar is None:
        status_bar = AgentStatusBar(
            workspace,
            profile=profile.name,
            model=profile.model or settings.CODING_LLM_MODEL,
            completion_gate=completion_gate,
        )
    if (memory_provider is not None or memory_writer is not None) != (memory_scope is not None):
        raise ValueError("外部记忆需要同时提供 Provider/Writer 和 scope")
    memory_selector = None
    if memory_provider is not None:
        memory_selector = MemoryRecallSelector(
            memory_provider, memory_scope,
            count_tokens=context_manager.count_tokens,
            available_tokens=lambda messages: max(
                0, context_manager.max_tokens
                - context_manager.count_request_tokens(messages) - 64,
            ),
        )
    request_view = RequestView(
        context_selector=context_selector,
        status_bar=status_bar,
        memory_selector=memory_selector,
    )
    if completion_gate is not None:
        hooks.on("pre_tool", completion_gate.before_tool)
        hooks.on("post_tool", completion_gate.after_tool)
    if budget is None:
        budget = BudgetPolicy(max_turns=settings.CODING_MAX_TURNS)

    return RuntimeComponents(
        tools=tools,
        permission=permission,
        guard=guard,
        context_manager=context_manager,
        context_selector=context_selector,
        request_view=request_view,
        completion_gate=completion_gate,
        status_bar=status_bar,
        hooks=hooks,
        budget=budget,
        context_info=context_info,
        token_budget=token_budget,
        memory_selector=memory_selector,
        memory_writer=memory_writer,
        memory_scope=memory_scope,
    )


def create_runtime(
    workspace,
    model_fn,
    tools=None,
    hooks=None,
    session_store=None,
    context_manager=None,
    context_selector=None,
    completion_gate=None,
    permission=None,
    guard=None,
    budget=None,
    base_injections=None,
    auto_approve=False,
    loop_class=None,
    profile=None,
    status_bar=None,
    runtime_components=None,
    memory_provider=None,
    memory_writer=None,
    memory_scope=None,
):
    """创建 AutoCoding Machine Runtime。

    参数允许调用方替换内部 adapter：
    - hooks 可以提前注册生命周期回调。
    - tools 可以使用不同输出上限。
    - base_injections 是 Instructions/Skills 等已经构造好的快照。
    - runtime_components 可以复用入口已经准备好的同一组组件。
    """
    if runtime_components is not None and (memory_provider is not None or memory_writer is not None):
        raise ValueError("提供外部记忆时请由 Factory 创建组件")
    profile = profile or Profile()
    runtime_components = runtime_components or build_runtime_components(
        workspace=workspace,
        profile=profile,
        tools=tools,
        hooks=hooks,
        session_store=session_store,
        context_manager=context_manager,
        context_selector=context_selector,
        completion_gate=completion_gate,
        permission=permission,
        guard=guard,
        budget=budget,
        auto_approve=auto_approve,
        status_bar=status_bar,
        memory_provider=memory_provider,
        memory_writer=memory_writer,
        memory_scope=memory_scope,
    )
    tools = runtime_components.tools
    permission = runtime_components.permission
    guard = runtime_components.guard
    context_manager = runtime_components.context_manager
    context_selector = runtime_components.context_selector
    request_view = runtime_components.request_view
    completion_gate = runtime_components.completion_gate
    status_bar = runtime_components.status_bar
    hooks = runtime_components.hooks
    budget = runtime_components.budget
    context = RuntimeContext(
        workspace=workspace, profile=profile.name,
        memory_manager=getattr(getattr(tools, "sandbox", None), "memory_manager", None),
    )
    registry = RuntimeRegistry(context, tools=tools.get_manager(), hooks=hooks)
    # Profile 负责注册 Coding 专属能力，Factory 不复制具体策略。
    if profile.kind == "coding":
        CodingProfile().register(registry)
    else:
        registry.use(MarkdownMemoryExtension())

    # 所有入口使用同一套注入；CLI 可传启动时已读取的快照，避免重复扫描。
    if base_injections is None:
        instructions = load_instructions(Path(workspace)) if profile.load_instructions else {}
        skills = getattr(getattr(tools, "sandbox", None), "skills", []) or []
        base_injections = build_injections(instructions, skills if "load_skill" in profile.tools else [])

    # 显式注入按原有顺序注册。
    if base_injections:
        for index, value in enumerate(base_injections):
            registry.register_injection_value(f"base_{index}", value)
    trace = None
    # 普通聊天只需要原始 Session；详细 Trace 会重复保存请求内容，按 Profile 显式开启。
    if session_store is not None and profile.trace_enabled:
        trace = RunTrace(
            profile.state_dir(workspace) / "runs", profile, model_fn,
            getattr(getattr(tools, "sandbox", None), "skills", []) or [],
            getattr(context_manager, "max_tokens", None),
        )
        trace.set_session(session_store)
        model_fn = trace.call
        hooks.on_event(trace.on_event)
    # loop_class 只用于测试或特殊入口注入，默认仍使用正式 MachineLoop。
    loop_type = loop_class or MachineLoop
    loop = loop_type(
        model_fn=model_fn,
        tools=tools,
        permission=permission,
        guard=guard,
        budget=budget,
        final_verifier=lambda msgs, resp: resp.done,
        hooks=hooks,
        context_manager=context_manager,
        context_selector=context_selector,
        request_view=request_view,
        session_store=session_store,
        completion_gate=completion_gate,
        status_bar=status_bar,
    )
    return AgentRuntime(
        system_prompt=profile.prompt or get_system_prompt(str(workspace)),
        loop=loop,
        registry=registry,
        components={
            "tools": tools,
            "permission": permission,
            "guard": guard,
            "hooks": hooks,
            "context_manager": context_manager,
            "context_selector": context_selector,
            "request_view": request_view,
            "completion_gate": completion_gate,
            "status_bar": status_bar,
            "session_store": session_store,
            "memory_writer": runtime_components.memory_writer,
            "memory_scope": runtime_components.memory_scope,
            "trace": trace,
        },
    )


def create_coding_runtime(workspace, model_fn, **components):
    """兼容原有 Coding 调用；新入口统一使用 create_runtime。"""
    return create_runtime(workspace, model_fn, **components)


def open_harness_session(
    workspace,
    model_fn,
    *,
    profile=None,
    tools=None,
    resume=None,
    session_store=None,
    runtime_components=None,
    base_injections=None,
    memory_provider=None,
    memory_writer=None,
    memory_scope=None,
    on_event=None,
):
    """用默认组装路径打开会话；终端入口可传入已准备好的组件。"""
    if on_event is not None and not callable(on_event):
        raise TypeError("on_event 必须是可调用对象")
    profile = profile or Profile()
    if tools is not None and runtime_components is not None:
        raise ValueError("tools 与 runtime_components 不能同时提供")
    if (memory_provider is not None or memory_writer is not None) != (memory_scope is not None):
        raise ValueError("外部记忆需要同时提供 Provider/Writer 和 scope")
    if session_store is None:
        session_store, history, error = open_session(
            profile.state_dir(workspace) / "sessions", resume
        )
        if error:
            raise ValueError(error)
    else:
        history = session_store.load()
    runtime = create_runtime(
        workspace=workspace,
        model_fn=model_fn,
        profile=profile,
        tools=tools,
        session_store=session_store,
        runtime_components=runtime_components,
        base_injections=base_injections,
        memory_provider=memory_provider,
        memory_writer=memory_writer,
        memory_scope=memory_scope,
    )
    if on_event is not None:
        runtime.hooks.on_event(_public_event_observer(on_event))
    session = runtime.create_session(session_store, history)
    if resume is not None:
        session.repair_interrupted()
    return session
