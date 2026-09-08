"""从外部行为验证 Profile：不靠 prompt 保证权限，也不接真实模型。"""

from dataclasses import replace
import json

import pytest

from src.engine import CancellationToken, ContextManager, SessionStore
from src.engine.contracts import AgentResponse, PermissionDecision, ToolCall
from src.engine.session_store import open_session
from src.profiles.config import load_profile
from src.runtime.factory import create_runtime
from src.runtime.tools import ProfileTools


def call(tool_name, **arguments):
    return ToolCall(id="test", name=tool_name, arguments=arguments)


@pytest.fixture(autouse=True)
def isolated_skills(tmp_path, monkeypatch):
    # 不扫描开发者电脑上的真实技能，也不写真实用户记忆。
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    monkeypatch.setattr("src.config.settings.settings.MEMORY_ENABLED", True)


def test_review_denies_writes_even_with_auto_approve(tmp_path):
    runtime = create_runtime(
        tmp_path, lambda _: AgentResponse(content="建议", done=True),
        profile=load_profile("review"), context_manager=ContextManager(max_tokens=10000),
        auto_approve=True,
    )
    for name in ("write_file", "edit_file", "run_bash", "run_test", "memory"):
        request = call(name, path="oops.py", content="bad")
        assert runtime.permission.check(request) == PermissionDecision.DENY
        assert runtime.tools.execute(request).error
    assert not (tmp_path / "oops.py").exists()
    assert runtime.completion_gate is None


def test_skills_menu_search_and_load_share_allowlist(tmp_path):
    for name in ("allowed", "hidden"):
        path = tmp_path / ".agents" / "skills" / name / "SKILL.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"---\nname: {name}\ndescription: example\n---\n{name} instructions", encoding="utf-8")
    profile = replace(load_profile("review"), skills=("allowed",))
    tools = ProfileTools(tmp_path, profile)
    assert [item["name"] for item in tools.sandbox.skills] == ["allowed"]
    assert "hidden" not in tools.execute(call("search_skills")).content
    assert tools.execute(call("load_skill", name="hidden")).error
    assert "allowed instructions" in tools.execute(call("load_skill", name="allowed")).content


@pytest.mark.parametrize("selection", [(), None])
def test_empty_skills_hide_and_disable_skill_tools(tmp_path, selection):
    # 显式关闭技能、或扫描不到技能，都不应向模型提供空入口。
    profile = replace(load_profile("companion"), skills=selection)
    tools = ProfileTools(tmp_path, profile)
    names = {item["function"]["name"] for item in tools.get_schemas()}
    assert names == {"memory", "recall_history"}
    for name in ("search_skills", "load_skill"):
        assert tools.execute(call(name, name="missing")).error


@pytest.mark.parametrize("query", [None, "", "测试"])
def test_empty_skill_search_explains_profile_scope(tmp_path, query):
    # 旧入口仍可直接调用工具；空清单不能建议模型反复换关键词。
    from src.profiles.coding.tools.search_skills import execute

    tools = ProfileTools(tmp_path, load_profile("companion"))
    arguments = {} if query is None else {"query": query}
    result = execute(call("search_skills", **arguments), tools.sandbox, 2000)
    assert result.metadata["count"] == 0
    assert "当前 Profile 没有可用技能" in result.content
    assert "None" not in result.content
    assert "可以不传 query" not in result.content


def test_profiles_do_not_share_memory_or_history(tmp_path):
    first = replace(load_profile("companion"), name="alice")
    second = replace(first, name="bob")
    alice, bob = ProfileTools(tmp_path, first), ProfileTools(tmp_path, second)
    result = alice.execute(call("memory", action="add", target="user", content="喜欢银河咖啡"))
    assert not result.error
    assert "银河咖啡" in alice.sandbox.memory_manager.load("user")
    assert bob.sandbox.memory_manager.load("user") == ""
    store = SessionStore(alice.sandbox.sessions_dir, "history")
    store.append({"role": "user", "content": "银河咖啡"})
    assert alice.execute(call("recall_history", query="银河咖啡")).metadata["matches"] > 0
    assert bob.execute(call("recall_history", query="银河咖啡")).metadata["matches"] == 0
    assert open_session(bob.sandbox.sessions_dir, "history")[2] is not None


@pytest.mark.parametrize("target", ["../alice/history", "..\\alice\\history", "C:history", ".."])
def test_resume_cannot_escape_profile(tmp_path, target):
    assert open_session(tmp_path, target)[2] is not None


def test_companion_uses_shared_loop_and_separate_trace(tmp_path):
    # Companion 不加载仓库 Coding 指令，也不要求修改代码才能完成。
    (tmp_path / "AGENTS.md").write_text("CODING_ONLY", encoding="utf-8")
    profile = replace(load_profile("companion"), trace_enabled=True)
    store = SessionStore(profile.state_dir(tmp_path) / "sessions", "chat")
    runtime = create_runtime(
        tmp_path, lambda _: AgentResponse(content="今天过得怎么样？", done=True),
        profile=profile, session_store=store, context_manager=ContextManager(max_tokens=10000),
    )
    messages = runtime.build_messages([{"role": "user", "content": "你好"}])
    assert "CODING_ONLY" not in str(messages)
    assert runtime.run(messages, CancellationToken())["status"] == "success"
    assert store.load() == [{"role": "assistant", "content": "今天过得怎么样？"}]
    records = [json.loads(line) for line in runtime.trace.path.read_text(encoding="utf-8").splitlines()]
    assert {item["event"] for item in records} >= {"configuration", "model_request", "model_response", "done"}
    assert records[0]["profile"]["name"] == "companion"
    assert runtime.trace.path.parent != store.path.parent
    # /resume 后，诊断与会话必须一起切换。
    new_store = SessionStore(store.path.parent, "next")
    runtime.set_session_store(new_store)
    assert runtime.trace.path.stem == "next"
    assert runtime.context_selector.current_session_id == "next"


def test_trace_is_disabled_by_default(tmp_path):
    profile = load_profile("companion")
    store = SessionStore(profile.state_dir(tmp_path) / "sessions", "chat")
    runtime = create_runtime(
        tmp_path, lambda _: AgentResponse(content="你好", done=True),
        profile=profile, session_store=store,
        context_manager=ContextManager(max_tokens=10000),
    )
    assert runtime.trace is None
    assert not (profile.state_dir(tmp_path) / "runs").exists()


def test_custom_profile_config_and_legacy_paths(tmp_path):
    path = tmp_path / "review.yaml"
    path.write_text("name: my-review\nkind: review\nskills: []\nmodel: local-model\ncontext_budget: 12000\nprompt: 请审查代码\n", encoding="utf-8")
    profile = load_profile(str(path))
    assert profile.skills == ()
    assert profile.model == "local-model"
    assert profile.context_budget == 12000
    assert not profile.verify_changes
    assert load_profile("coding").state_dir(tmp_path) == tmp_path / ".autocoding/profiles/coding"
    assert profile.state_dir(tmp_path) == tmp_path / ".autocoding/profiles/my-review"


@pytest.mark.parametrize("config", [
    "name: ../escape", "name: coding", "name: mine\nkind: unknown",
    "name: mine\nkind: review\ntools: [run_bash]", "name: mine\nskills: nope",
    "name: mine\ncontext_budget: true", "name: mine\nload_instructions: yesplease",
    "name: mine\ntrace_enabled: 1",
    "name: mine\nunknown: 1",
])
def test_invalid_config_fails_before_runtime(tmp_path, config):
    path = tmp_path / "bad.yaml"
    path.write_text(config, encoding="utf-8")
    with pytest.raises(ValueError):
        load_profile(str(path))


@pytest.mark.parametrize("profile_name", ["review", "companion"])
def test_cli_selects_profile_and_runs_without_real_model(tmp_path, monkeypatch, profile_name):
    """真正走 CLI 组装与循环，只替换输入和网络，验证命令行参数贯通。"""
    from src.profiles.coding import cli, llm_adapter

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "profile_token_budget", lambda _, **kwargs: 10000)
    monkeypatch.setattr(cli, "resolve_context_info", lambda _: {"window": None, "source": "未知"})
    monkeypatch.setattr(cli, "create_main_session", lambda *args, **kwargs: None)
    inputs = iter(["你好", "/memory", "/quit"])
    monkeypatch.setattr(cli, "main_input", lambda *args: next(inputs))
    requests = []

    def respond(messages, **kwargs):
        requests.append((messages, kwargs))
        return {"content": "你好呀", "tool_calls": [], "usage": {}}

    monkeypatch.setattr(llm_adapter, "chat_stream", respond)
    cli.run_cli(profile=profile_name)
    assert len(requests) == 1
    schemas = requests[0][1]["tools"]
    assert "write_file" not in {item["function"]["name"] for item in schemas}
    session_dir = load_profile(profile_name).state_dir(tmp_path) / "sessions"
    store, history, error = open_session(session_dir, "")
    assert error is None
    assert history[-1]["content"] == "你好呀"


def test_custom_model_does_not_change_global_adapter(tmp_path, monkeypatch):
    from src.common.model_adapter import ModelAdapter
    from src.runtime.context import make_summarizer
    from src.config.settings import settings

    default_model = settings.CODING_LLM_MODEL
    local = ModelAdapter([], model="local-qwen")
    assert local.model == "local-qwen"
    assert ModelAdapter([]).model == default_model
    monkeypatch.setattr(settings, "CONTEXT_SUMMARY_ENABLED", True)
    seen = []
    monkeypatch.setattr("src.runtime.context.chat", lambda messages, **kwargs: seen.append(kwargs) or "摘要")
    assert make_summarizer(model="local-qwen")([{"role": "user", "content": "你好"}]) == "摘要"
    assert seen[0]["model"] == "local-qwen"
