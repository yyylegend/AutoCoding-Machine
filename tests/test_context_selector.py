"""轻量自动上下文选择测试。"""

from unittest.mock import Mock, patch

from src.engine import AgentResponse, BudgetPolicy, CancellationToken, MachineLoop
from src.engine.session_store import SessionStore, sessions_dir_for
from src.profiles.coding.context_selector import ContextSelector
from src.runtime.factory import create_coding_runtime


def test_selector_injects_relevant_history_without_mutating_messages(tmp_path):
    sessions_dir = sessions_dir_for(tmp_path)
    old_store = SessionStore(sessions_dir, "old-session")
    old_store.append({"role": "user", "content": "继续处理上下文压缩超时问题"})
    old_store.append({"role": "assistant", "content": "已经增加摘要失败冷却和摘录兜底"})

    messages = [
        {"role": "system", "content": "你是 Coding Agent"},
        {"role": "user", "content": "继续看看上下文压缩的超时兜底"},
    ]
    original = list(messages)
    selector = ContextSelector(tmp_path, current_session_id="current-session")

    selected = selector.select(messages)

    assert messages == original
    assert selected is not messages
    assert len(selected) == len(messages) + 1
    assert selected[1]["role"] == "system"
    assert "自动召回" in selected[1]["content"]
    assert "old-session" in selected[1]["content"]


def test_machine_loop_uses_selected_view_without_persisting_injection(tmp_path):
    store = SessionStore(sessions_dir_for(tmp_path), "current-session")
    seen = []

    class FakeSelector:
        def select(self, messages):
            selected = list(messages)
            selected.insert(1, {"role": "system", "content": "临时召回内容"})
            return selected

    def model_fn(messages):
        seen.extend(messages)
        return AgentResponse(content="完成", done=True)

    loop = MachineLoop(
        model_fn=model_fn,
        tools=Mock(),
        permission=Mock(),
        guard=Mock(),
        budget=BudgetPolicy(max_turns=2),
        final_verifier=lambda messages, response: response.done,
        session_store=store,
        context_selector=FakeSelector(),
    )
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "继续任务"},
    ]

    result = loop.run(messages, CancellationToken())

    assert result["status"] == "success"
    assert any(message.get("content") == "临时召回内容" for message in seen)
    assert all(message.get("content") != "临时召回内容" for message in messages)
    assert all(message.get("content") != "临时召回内容" for message in store.load())


def test_runtime_enables_selector_and_excludes_current_session(tmp_path):
    store = SessionStore(sessions_dir_for(tmp_path), "current-session")

    runtime = create_coding_runtime(
        workspace=tmp_path,
        model_fn=Mock(),
        session_store=store,
    )

    selector = runtime.loop.context_selector
    assert isinstance(selector, ContextSelector)
    assert selector.current_session_id == "current-session"


def test_selector_failure_does_not_block_agent_request(tmp_path):
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "继续任务"},
    ]
    selector = ContextSelector(tmp_path)

    with patch(
        "src.profiles.coding.context_selector.search_history",
        side_effect=OSError("history unavailable"),
    ):
        selected = selector.select(messages)

    assert selected is messages


def test_selector_excludes_current_session_and_skips_unrelated_history(tmp_path):
    sessions_dir = sessions_dir_for(tmp_path)
    current = SessionStore(sessions_dir, "current-session")
    current.append({"role": "user", "content": "上下文压缩超时兜底"})
    old = SessionStore(sessions_dir, "old-session")
    old.append({"role": "user", "content": "数据库迁移和索引优化"})
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "继续上下文压缩超时兜底"},
    ]

    selected = ContextSelector(
        tmp_path,
        current_session_id="current-session",
    ).select(messages)

    assert selected is messages


def test_selector_limits_recalled_hits_and_characters(tmp_path):
    sessions_dir = sessions_dir_for(tmp_path)
    for index in range(3):
        store = SessionStore(sessions_dir, "old-" + str(index))
        store.append({
            "role": "user",
            "content": "上下文压缩超时重试 " + ("细节" * 100),
        })
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "继续上下文压缩超时重试"},
    ]

    selected = ContextSelector(tmp_path, max_hits=2, max_chars=180).select(messages)

    recalled = selected[1]["content"].split("\n\n", 1)[1]
    assert recalled.count("[session ") <= 2
    assert len(recalled) <= 180


def test_selector_reuses_result_for_same_user_message(tmp_path):
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "继续上下文压缩问题"},
    ]
    selector = ContextSelector(tmp_path)
    result = {
        "content": "[session old-session] 历史内容",
        "matches": 1,
    }

    with patch(
        "src.profiles.coding.context_selector.search_history",
        return_value=result,
    ) as search:
        first = selector.select(messages)
        second = selector.select(messages)

    assert search.call_count == 1
    assert first == second


def test_selector_allows_single_distinctive_query_token(tmp_path):
    store = SessionStore(sessions_dir_for(tmp_path), "old-session")
    store.append({"role": "user", "content": "config.py"})
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "config.py"},
    ]

    selected = ContextSelector(tmp_path).select(messages)

    assert len(selected) == len(messages) + 1
    assert "old-session" in selected[1]["content"]


def test_runtime_session_switch_updates_loop_and_selector(tmp_path):
    first = SessionStore(sessions_dir_for(tmp_path), "first-session")
    second = SessionStore(sessions_dir_for(tmp_path), "second-session")
    runtime = create_coding_runtime(
        workspace=tmp_path,
        model_fn=Mock(),
        session_store=first,
    )

    runtime.set_session_store(second)

    assert runtime.session_store is second
    assert runtime.loop.session_store is second
    assert runtime.context_selector.current_session_id == "second-session"
