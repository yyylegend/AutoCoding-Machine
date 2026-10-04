"""外部记忆只读契约：作用域、预算、临时注入与降级。"""

import pytest

from auto_coding_machine.engine import AgentResponse
from auto_coding_machine.memory import (
    InMemoryMemoryProvider,
    MemoryHit,
    MemoryQuery,
    MemoryScope,
)
from auto_coding_machine.memory.recall import MemoryRecallSelector
from auto_coding_machine.profiles.config import load_profile
from auto_coding_machine.runtime import open_harness_session


def test_in_memory_provider_isolates_exact_scope_and_caps_hits():
    alice = MemoryScope(user_id="alice", team_id="team-a", agent_id="coding")
    bob = MemoryScope(user_id="bob", team_id="team-a", agent_id="coding")
    other_team = MemoryScope(user_id="alice", team_id="team-b", agent_id="coding")
    provider = InMemoryMemoryProvider([
        MemoryHit("SQLite migration", "note-1", alice),
        MemoryHit("SQLite backup", "note-2", alice),
        MemoryHit("SQLite secret", "note-3", bob),
        MemoryHit("SQLite plan", "note-4", other_team),
    ])

    hits = provider.recall(MemoryQuery("SQLite", alice, max_items=1, max_tokens=100, timeout_seconds=2))

    assert [(hit.text, hit.source) for hit in hits] == [("SQLite migration", "note-1")]


@pytest.mark.parametrize("scope", [
    {"user_id": ""},
    {"user_id": "alice", "team_id": ""},
    {"user_id": "alice", "agent_id": ""},
])
def test_scope_rejects_blank_identifiers(scope):
    with pytest.raises(ValueError):
        MemoryScope(**scope)


def test_recall_selector_enforces_scope_items_and_token_budget_without_mutating_messages():
    scope = MemoryScope(user_id="alice", team_id="team-a")

    class UntrustedProvider:
        def recall(self, query):
            assert query.scope == scope
            assert query.timeout_seconds == 2
            return [
                MemoryHit("private", "wrong-user", MemoryScope(user_id="bob", team_id="team-a")),
                MemoryHit("short", "note-1", scope),
                MemoryHit("too-long-to-fit", "note-2", scope),
            ]

    selector = MemoryRecallSelector(
        UntrustedProvider(), scope, max_items=1, max_tokens=100,
        timeout_seconds=2, count_tokens=lambda messages: len(messages[0]["content"]),
    )
    messages = [{"role": "system", "content": "system"}, {"role": "user", "content": "question"}]

    selected = selector.select(messages)

    assert selected is not messages
    assert messages == [{"role": "system", "content": "system"}, {"role": "user", "content": "question"}]
    assert len(selected) == 3
    assert "short" in selected[1]["content"]
    assert "wrong-user" not in selected[1]["content"]
    assert "too-long" not in selected[1]["content"]


def test_recall_selector_skips_items_that_exceed_token_budget():
    scope = MemoryScope(user_id="alice")
    provider = InMemoryMemoryProvider([MemoryHit("x" * 1000, "note", scope)])
    selector = MemoryRecallSelector(provider, scope, max_tokens=50)
    messages = [{"role": "user", "content": "x"}]

    assert selector.select(messages) is messages


def test_recall_selector_skips_provider_when_request_has_no_room():
    class NeverCalled:
        def recall(self, query):
            raise AssertionError("请求已满，不应召回")

    selector = MemoryRecallSelector(
        NeverCalled(), MemoryScope(user_id="alice"), available_tokens=lambda _messages: 0,
    )
    messages = [{"role": "user", "content": "hello"}]

    assert selector.select(messages) is messages


def test_recall_selector_refreshes_same_question_in_new_run():
    scope = MemoryScope(user_id="alice")
    provider = InMemoryMemoryProvider([MemoryHit("first", "note-1", scope)])
    selector = MemoryRecallSelector(provider, scope)
    first = [{"role": "user", "content": "first"}]

    assert "first" in selector.select(first)[0]["content"]
    provider.hits = [MemoryHit("second first", "note-2", scope)]
    second = [{"role": "user", "content": "first"}]
    assert "second first" in selector.select(second)[0]["content"]


def test_recall_failure_is_visible_and_skipped(caplog):
    class BrokenProvider:
        def recall(self, query):
            raise TimeoutError("service timeout")

    selector = MemoryRecallSelector(BrokenProvider(), MemoryScope(user_id="alice"))
    messages = [{"role": "user", "content": "hello"}]

    assert selector.select(messages) is messages
    assert "外部记忆召回失败" in caplog.text


def test_invalid_provider_result_is_visible_and_skipped(caplog):
    class InvalidProvider:
        def recall(self, query):
            return None

    messages = [{"role": "user", "content": "hello"}]
    selector = MemoryRecallSelector(InvalidProvider(), MemoryScope(user_id="alice"))

    assert selector.select(messages) is messages
    assert "外部记忆召回失败" in caplog.text


def test_public_entry_uses_temporary_external_memory_without_persisting_it(tmp_path, monkeypatch):
    from auto_coding_machine.runtime import factory

    monkeypatch.setattr(factory, "resolve_context_info", lambda _model: {"window": None, "source": "test"})
    monkeypatch.setattr(factory, "profile_token_budget", lambda _profile, **_kwargs: 8000)
    scope = MemoryScope(user_id="alice")
    provider = InMemoryMemoryProvider([MemoryHit("SQLite backup", "note-1", scope)])
    seen = []

    def model_fn(messages):
        seen.append(list(messages))
        return AgentResponse(content="完成", done=True)

    session = open_harness_session(
        tmp_path, model_fn, profile=load_profile("review"),
        memory_provider=provider, memory_scope=scope,
    )
    assert session.begin_run("SQLite").start()["status"] == "success"
    assert any("SQLite backup" in str(message) for message in seen[0])
    assert all("SQLite backup" not in str(message) for message in session.store.load())


def test_public_entry_requires_scope_with_provider(tmp_path):
    with pytest.raises(ValueError):
        open_harness_session(tmp_path, lambda _messages: None, memory_provider=InMemoryMemoryProvider())
