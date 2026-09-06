"""旧 Coding 状态迁移测试：只移动，不覆盖、不丢失。"""

from src.runtime.state import migrate_legacy_coding_state


def test_migrate_legacy_coding_state(tmp_path):
    root = tmp_path / ".autocoding"
    (root / "sessions").mkdir(parents=True)
    (root / "runs").mkdir()
    (root / "sessions" / "old.jsonl").write_text("session", encoding="utf-8")
    (root / "runs" / "old.jsonl").write_text("trace", encoding="utf-8")
    (root / "input_history").write_text("hello\n", encoding="utf-8")

    moved = migrate_legacy_coding_state(tmp_path)

    target = root / "profiles" / "coding"
    assert sorted(path.name for path in moved) == ["input_history", "old.jsonl", "old.jsonl"]
    assert (target / "sessions" / "old.jsonl").read_text(encoding="utf-8") == "session"
    assert (target / "runs" / "old.jsonl").read_text(encoding="utf-8") == "trace"
    assert (target / "input_history").read_text(encoding="utf-8") == "hello\n"
    assert not (root / "sessions").exists()
    assert not (root / "runs").exists()
    assert not (root / "input_history").exists()


def test_migration_does_not_overwrite_existing_target(tmp_path):
    root = tmp_path / ".autocoding"
    (root / "sessions").mkdir(parents=True)
    (root / "profiles" / "coding" / "sessions").mkdir(parents=True)
    (root / "sessions" / "same.jsonl").write_text("old", encoding="utf-8")
    (root / "profiles" / "coding" / "sessions" / "same.jsonl").write_text("new", encoding="utf-8")

    moved = migrate_legacy_coding_state(tmp_path)

    assert moved == []
    assert (root / "sessions" / "same.jsonl").read_text(encoding="utf-8") == "old"
    assert (root / "profiles" / "coding" / "sessions" / "same.jsonl").read_text(encoding="utf-8") == "new"
