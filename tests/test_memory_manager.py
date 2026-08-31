"""MemoryManager 单元测试（纯文件逻辑，不碰 LLM）。

覆盖：
  - add：正常追加 / 自动建目录 / 重复拒绝 / 超限报错
  - replace：唯一匹配 / 零匹配 / 多匹配 / 换长文超限
  - remove：唯一匹配 / 零匹配 / 多匹配
  - render_injection：容量头格式 / 两文件为空返回空串 / 超长文件截断兜底
"""

import pytest

from src.engine.memory_manager import MemoryManager


@pytest.fixture
def manager(tmp_path):
    """建一个指向临时目录的 MemoryManager，上限设小方便测容量。"""
    return MemoryManager(
        memory_path=tmp_path / ".autocoding" / "MEMORY.md",
        user_path=tmp_path / "home" / ".autocoding" / "USER.md",
        memory_limit=100,
        user_limit=60,
    )


# ============================================================
#  add
# ============================================================


def test_add_creates_file_and_dirs(manager, tmp_path):
    """add 应自动创建父目录和文件，条目带 '- ' 前缀。"""
    result = manager.add("memory", "测试命令是 pytest -q")
    assert result["ok"] is True

    path = tmp_path / ".autocoding" / "MEMORY.md"
    assert path.is_file()
    assert path.read_text(encoding="utf-8") == "- 测试命令是 pytest -q\n"


def test_add_appends_new_line(manager):
    """第二条应追加在第一条后面，一行一条。"""
    manager.add("memory", "第一条")
    manager.add("memory", "第二条")
    text = manager.load("memory")
    assert text == "- 第一条\n- 第二条\n"


def test_add_multiline_content_flattened(manager):
    """多行内容应压成一行，保证'一行一条'格式不破。"""
    manager.add("memory", "第一行\n第二行")
    text = manager.load("memory")
    assert text == "- 第一行 第二行\n"


def test_add_duplicate_rejected(manager):
    """完全相同的条目不重复添加（ok=True 但文件不变）。"""
    manager.add("memory", "同一件事")
    result = manager.add("memory", "同一件事")
    assert result["ok"] is True
    assert "已存在" in result["message"]
    # 文件里仍然只有一条
    assert manager.load("memory").count("同一件事") == 1


def test_add_over_limit_returns_error_with_entries(manager):
    """超限报错：错误信息要带当前条目和 replace/remove 提示。"""
    manager.add("memory", "旧条目内容")
    # 上限 100 字符，塞一条很长的必超
    result = manager.add("memory", "x" * 200)
    assert result["ok"] is False
    assert "已满" in result["message"]
    assert "旧条目内容" in result["message"]   # 附了当前条目
    assert "replace" in result["message"]      # 给了修正提示
    # 文件没被写坏，还是只有旧条目
    assert manager.load("memory") == "- 旧条目内容\n"


def test_add_invalid_target(manager):
    """target 不合法直接报错。"""
    result = manager.add("wrong", "内容")
    assert result["ok"] is False


# ============================================================
#  replace
# ============================================================


def test_replace_unique_match(manager):
    """唯一匹配：旧条目被换成新内容。"""
    manager.add("memory", "用户喜欢深色模式")
    result = manager.replace("memory", "深色模式", "用户喜欢浅色模式")
    assert result["ok"] is True
    text = manager.load("memory")
    assert "浅色模式" in text
    assert "深色模式" not in text


def test_replace_no_match(manager):
    """零匹配报错，并附当前条目提示。"""
    manager.add("memory", "某条笔记")
    result = manager.replace("memory", "不存在的字", "新内容")
    assert result["ok"] is False
    assert "没有找到" in result["message"]
    assert "某条笔记" in result["message"]


def test_replace_multiple_match(manager):
    """多匹配报错，提示换更精确的 old_text。"""
    manager.add("memory", "命令 A 用法")
    manager.add("memory", "命令 B 用法")
    result = manager.replace("memory", "命令", "新内容")
    assert result["ok"] is False
    assert "更精确" in result["message"]


def test_replace_over_limit(manager):
    """换成更长的内容也可能超限，同样报错且不写盘。"""
    manager.add("memory", "短条目")
    result = manager.replace("memory", "短条目", "y" * 200)
    assert result["ok"] is False
    assert "已满" in result["message"]
    assert manager.load("memory") == "- 短条目\n"


# ============================================================
#  remove
# ============================================================


def test_remove_unique_match(manager):
    """唯一匹配：条目被删掉，其它条目保留。"""
    manager.add("memory", "要删的条目")
    manager.add("memory", "要留的条目")
    result = manager.remove("memory", "要删的")
    assert result["ok"] is True
    text = manager.load("memory")
    assert "要删的条目" not in text
    assert "要留的条目" in text


def test_remove_no_match(manager):
    """零匹配报错。"""
    result = manager.remove("memory", "不存在")
    assert result["ok"] is False
    assert "没有找到" in result["message"]


def test_remove_multiple_match(manager):
    """多匹配报错。"""
    manager.add("memory", "重复词 一")
    manager.add("memory", "重复词 二")
    result = manager.remove("memory", "重复词")
    assert result["ok"] is False
    assert "更精确" in result["message"]


# ============================================================
#  render_injection
# ============================================================


def test_render_injection_empty(manager):
    """两个文件都不存在/为空 → 返回空串（调用方跳过注入）。"""
    assert manager.render_injection() == ""


def test_render_injection_headers(manager):
    """有内容时：带前言、两个段落各带容量头。"""
    manager.add("memory", "项目笔记一条")
    manager.add("user", "用户画像一条")
    text = manager.render_injection()
    assert "长期记忆" in text                # 前言
    assert "MEMORY 项目笔记" in text          # memory 段头
    assert "USER 用户画像" in text            # user 段头
    assert "/100 字符】" in text              # memory 容量头（上限 100）
    assert "/60 字符】" in text               # user 容量头（上限 60）
    assert "- 项目笔记一条" in text
    assert "- 用户画像一条" in text


def test_render_injection_only_memory(manager):
    """只有 memory 有内容时，不出现 user 段。"""
    manager.add("memory", "只有这条")
    text = manager.render_injection()
    assert "MEMORY 项目笔记" in text
    assert "USER 用户画像" not in text


def test_render_injection_overlong_file_clipped(manager, tmp_path):
    """人手动把文件改超上限：渲染按上限截断并加提示，不崩。"""
    path = tmp_path / ".autocoding" / "MEMORY.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("- " + "z" * 500, encoding="utf-8")  # 远超上限 100

    text = manager.render_injection()
    assert "已截断" in text
    # 截断后的正文不应包含完整的 500 个 z
    assert "z" * 500 not in text
