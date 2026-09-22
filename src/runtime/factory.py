"""Coding Runtime 工厂。

CLI 和其他调用方都通过这个 seam 获取公共组件。
"""

from dataclasses import dataclass

from src.engine import (
    BudgetPolicy,
    GuardManager,
    HookManager,
    MachineLoop,
    PermissionManager,
)
from src.config.settings import settings
from src.profiles.coding.completion_gate import CompletionGate
from src.runtime.context_selector import ContextSelector
from src.runtime.context import build_context_manager, profile_token_budget, resolve_context_info
from src.engine.request_view import RequestView
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.system_prompt import get_system_prompt
from src.profiles.coding.profile import CodingProfile, MarkdownMemoryExtension
from src.runtime.registry import RuntimeContext, RuntimeRegistry
from src.runtime.runtime import AgentRuntime
from src.profiles.config import Profile
from src.runtime.tools import ProfileTools
from src.runtime.trace import RunTrace
from src.runtime.prompts import load_instructions, build_injections
from src.runtime.status import AgentStatusBar
from pathlib import Path


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

    if context_selector is None:
        current_session_id = getattr(session_store, "session_id", None)
        context_selector = ContextSelector(
            workspace=workspace,
            current_session_id=current_session_id,
            sessions_dir=profile.state_dir(workspace) / "sessions",
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
    request_view = RequestView(
        context_selector=context_selector,
        status_bar=status_bar,
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
):
    """创建 AutoCoding Machine Runtime。

    参数允许调用方替换内部 adapter：
    - hooks 可以提前注册生命周期回调。
    - tools 可以使用不同输出上限。
    - base_injections 是 Instructions/Skills 等已经构造好的快照。
    - runtime_components 可以复用入口已经准备好的同一组组件。
    """
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
            "trace": trace,
        },
    )


def create_coding_runtime(workspace, model_fn, **components):
    """兼容原有 Coding 调用；新入口统一使用 create_runtime。"""
    return create_runtime(workspace, model_fn, **components)
