"""保护路径策略测试。"""

from src.runtime.protected_paths import create_protected_path_check


def test_protected_path_check_blocks_sensitive_write_paths(tmp_path):
    check = create_protected_path_check(tmp_path)

    assert check("write_file", {"path": ".env"}) == "deny"
    assert check("edit_file", {"path": ".git/config"}) == "deny"
    assert check("write_file", {"path": ".autocoding/MEMORY.md"}) == "deny"
    assert check("write_file", {"path": "src/main.py"}) == "allow"


def test_protected_path_check_only_targets_write_tools(tmp_path):
    check = create_protected_path_check(tmp_path)
    assert check("read_file", {"path": ".env"}) == "allow"
