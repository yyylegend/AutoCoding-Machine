"""AgentSession 的会话视图与任务创建 seam 测试。"""

from src.engine.session_store import SessionStore
from src.runtime.session import AgentSession


class FakeRuntime:
    def __init__(self, context_manager):
        self.context_manager = context_manager
        self.created_runs = []
        self.switched_stores = []

    def create_run(self, messages):
        run = {"messages": messages}
        self.created_runs.append(run)
        return run

    def build_messages(self, history, extra_injections=None):
        return (
            [{"role": "system", "content": "stable"}]
            + list(extra_injections or [])
            + list(history)
        )

    def set_session_store(self, store):
        self.switched_stores.append(store)


class FakeContextManager:
    def __init__(self, compacted):
        self.compacted = compacted
        self.calls = []

    def maybe_compact(self, history, force=False):
        self.calls.append((list(history), force))
        return list(self.compacted)


def test_session_starts_run_from_persisted_history(tmp_path):
    store = SessionStore(tmp_path, "session-1")
    store.append({"role": "user", "content": "旧任务"})
    runtime = FakeRuntime(FakeContextManager([]))
    session = AgentSession(runtime, store, store.load())

    run = session.begin_run("新任务")

    assert run is runtime.created_runs[0]
    assert session.history[-1] == {"role": "user", "content": "新任务"}
    assert session.messages[-1] == {"role": "user", "content": "新任务"}
    assert store.load()[-1] == {"role": "user", "content": "新任务"}


def test_session_compaction_keeps_summary_and_later_jsonl_messages(tmp_path):
    store = SessionStore(tmp_path, "session-2")
    store.append({"role": "user", "content": "旧问题"})
    store.append({"role": "assistant", "content": "旧回答"})
    summary = {"role": "user", "content": "[历史摘要] 旧问题已讨论"}
    context_manager = FakeContextManager([summary])
    session = AgentSession(FakeRuntime(context_manager), store, store.load())

    changed, before, after = session.compact()
    session.append_message({"role": "user", "content": "新问题"})

    assert changed is True
    assert (before, after) == (2, 1)
    assert context_manager.calls[0][1] is True
    assert session.history == [summary, {"role": "user", "content": "新问题"}]
    assert session.messages[-2:] == session.history
    assert store.load() == [
        {"role": "user", "content": "旧问题"},
        {"role": "assistant", "content": "旧回答"},
        {"role": "user", "content": "新问题"},
    ]


def test_session_clear_hides_old_history_without_deleting_jsonl(tmp_path):
    store = SessionStore(tmp_path, "session-3")
    store.append({"role": "user", "content": "旧任务"})
    session = AgentSession(FakeRuntime(FakeContextManager([])), store, store.load())

    session.clear()
    session.append_message({"role": "user", "content": "新任务"})

    assert session.history == [{"role": "user", "content": "新任务"}]
    assert store.load() == [
        {"role": "user", "content": "旧任务"},
        {"role": "user", "content": "新任务"},
    ]


def test_session_switch_resets_view_and_updates_runtime_store(tmp_path):
    first = SessionStore(tmp_path, "first")
    first.append({"role": "user", "content": "第一段"})
    second = SessionStore(tmp_path, "second")
    second.append({"role": "user", "content": "第二段"})
    runtime = FakeRuntime(FakeContextManager([]))
    session = AgentSession(runtime, first, first.load())

    session.clear()
    session.switch(second, second.load())

    assert session.store is second
    assert session.history == [{"role": "user", "content": "第二段"}]
    assert session.messages[-1] == {"role": "user", "content": "第二段"}
    assert runtime.switched_stores == [second]


def test_session_repairs_interrupted_tool_call_and_refreshes_view(tmp_path):
    store = SessionStore(tmp_path, "session-4")
    store.append({
        "role": "assistant",
        "tool_calls": [{
            "id": "call-1",
            "type": "function",
            "function": {"name": "read_file", "arguments": "{}"},
        }],
    })
    session = AgentSession(
        FakeRuntime(FakeContextManager([])), store, store.load()
    )

    repaired = session.repair_interrupted()

    assert repaired == 1
    assert session.history[-1]["role"] == "tool"
    assert session.history[-1]["tool_call_id"] == "call-1"
    assert session.messages[-1] == session.history[-1]
