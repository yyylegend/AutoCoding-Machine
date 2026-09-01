"""Coding Runtime 工厂。

CLI 和其他调用方都通过这个 seam 获取公共组件。
"""

from src.engine import (
    BudgetPolicy,
    GuardManager,
    HookManager,
    MachineLoop,
    PermissionManager,
)
from src.config.settings import settings
from src.profiles.coding.completion_gate import CompletionGate
from src.profiles.coding.context_selector import ContextSelector
from src.profiles.coding.context_setup import build_context_manager
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.system_prompt import get_system_prompt
from src.profiles.coding.tools import CodingTools
from src.profiles.coding.profile import CodingProfile, MarkdownMemoryExtension
from src.runtime.registry import RuntimeContext, RuntimeRegistry
from src.runtime.runtime import AgentRuntime


def create_coding_runtime(
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
):
    """创建 AutoCoding Machine Runtime。

    参数允许调用方替换内部 adapter：
    - hooks 可以提前注册生命周期回调。
    - tools 可以使用不同输出上限。
    - base_injections 是 Instructions/Skills 等已经构造好的快照。
    """
    if tools is None:
        tools = CodingTools(workspace, max_output_chars=5000)
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
        context_manager = build_context_manager()
    if context_selector is None:
        current_session_id = getattr(session_store, "session_id", None)
        context_selector = ContextSelector(
            workspace=workspace,
            current_session_id=current_session_id,
        )
    if completion_gate is None:
        # 完成证据门是 Coding 专属策略，放 Coding Profile（见 docs/plans V2 计划）。
        # 它需要沙箱来做路径规范化：优先复用工具集合里的那个。
        sandbox = getattr(tools, "sandbox", None)
        if not isinstance(sandbox, WorkspaceSandbox):
            sandbox = WorkspaceSandbox(workspace)
        completion_gate = CompletionGate(sandbox)
    # 注册到两个 Hook：执行前拍基线快照，执行后记修改/验证版本
    hooks.on("pre_tool", completion_gate.before_tool)
    hooks.on("post_tool", completion_gate.after_tool)
    if budget is None:
        budget = BudgetPolicy(max_turns=settings.CODING_MAX_TURNS)

    context = RuntimeContext(workspace=workspace, profile="coding")
    registry = RuntimeRegistry(context, tools=tools.get_manager(), hooks=hooks)
    # Profile 负责注册 Coding 专属能力，Factory 不复制具体策略。
    CodingProfile().register(registry)

    # 先注册调用方提供的注入，保持原来的 Instructions/Skills → Memory 顺序。
    if base_injections:
        for index, value in enumerate(base_injections):
            registry.register_injection_value(f"base_{index}", value)
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
        session_store=session_store,
        completion_gate=completion_gate,
    )
    return AgentRuntime(
        system_prompt=get_system_prompt(str(workspace)),
        loop=loop,
        registry=registry,
        components={
            "tools": tools,
            "permission": permission,
            "guard": guard,
            "hooks": hooks,
            "context_manager": context_manager,
            "context_selector": context_selector,
            "completion_gate": completion_gate,
            "session_store": session_store,
        },
    )
