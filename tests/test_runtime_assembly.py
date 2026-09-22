"""Runtime 组件组装 seam 测试。"""

from src.engine import BudgetPolicy, HookManager
from src.profiles.config import Profile
from src.runtime.factory import (
    RuntimeComponents,
    build_runtime_components,
)


class FakeTools:
    def get_manager(self):
        return object()

    def get_schemas(self):
        return []


class FakeGate:
    def before_tool(self, **_):
        pass

    def after_tool(self, **_):
        pass


def test_runtime_component_builder_preserves_injected_adapters(tmp_path):
    profile = Profile(name="assembly-review", kind="review", tools=())
    tools = FakeTools()
    hooks = HookManager()
    context_manager = object()
    context_selector = object()
    completion_gate = FakeGate()
    permission = object()
    guard = object()
    budget = BudgetPolicy(max_turns=7)
    status_bar = object()

    components = build_runtime_components(
        workspace=tmp_path,
        profile=profile,
        tools=tools,
        hooks=hooks,
        context_manager=context_manager,
        context_selector=context_selector,
        completion_gate=completion_gate,
        permission=permission,
        guard=guard,
        budget=budget,
        status_bar=status_bar,
    )

    assert components.tools is tools
    assert components.hooks is hooks
    assert components.context_manager is context_manager
    assert components.context_selector is context_selector
    assert components.completion_gate is completion_gate
    assert components.permission is permission
    assert components.guard is guard
    assert components.budget is budget
    assert components.status_bar is status_bar


def test_runtime_components_are_a_single_shared_set():
    fields = dict(
        tools=object(),
        permission=object(),
        guard=object(),
        context_manager=object(),
        context_selector=object(),
        request_view=object(),
        completion_gate=object(),
        status_bar=object(),
        hooks=object(),
        budget=object(),
        context_info={"window": None, "source": "provided"},
        token_budget=None,
    )

    components = RuntimeComponents(**fields)

    assert components.tools is fields["tools"]
    assert components.context_info == {"window": None, "source": "provided"}
