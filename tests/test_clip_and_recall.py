"""clip_text 保头保尾 + recall_history 工具的刚需测试。

只测外部行为，不测内部实现（ADR-0004 / spec）。
"""

import json
import tempfile
import unittest
from pathlib import Path

from src.engine.contracts import ToolCall
from src.profiles.coding.tools.helpers import clip_text


# =====================================
# 接缝 1：clip_text 纯函数（3 条）
# =====================================

class TestClipText(unittest.TestCase):
    """clip_text 保头保尾截断。"""

    def test_no_truncation_if_under_limit(self):
        """不超限 → 原样返回，truncated=False。"""
        text = "hello world"
        result, truncated = clip_text(text, 100)
        self.assertEqual(result, text)
        self.assertFalse(truncated)

    def test_head_and_tail_kept(self):
        """超限 → 头部 + 省略标注 + 尾部，truncated=True。"""
        # 造一个 200 字符的文本：前 100 是 A，后 100 是 B
        text = "A" * 100 + "B" * 100
        result, truncated = clip_text(text, 100)

        self.assertTrue(truncated)
        # 头部有 A
        self.assertTrue(result.startswith("A"))
        # 尾部有 B
        self.assertTrue(result.endswith("B"))
        # 中间有省略标注
        self.assertIn("省略", result)
        # 总长度不超过 max_chars（允许标注本身占位）
        self.assertLessEqual(len(result), 120)  # 100 + 标注余量

    def test_none_and_empty(self):
        """空字符串 / None → 返回空，不崩。"""
        result, truncated = clip_text(None, 100)
        self.assertEqual(result, "")
        self.assertFalse(truncated)

        result, truncated = clip_text("", 100)
        self.assertEqual(result, "")
        self.assertFalse(truncated)


# =====================================
# 接缝 2：recall_history 工具级（3 条）
# =====================================

class TestRecallHistory(unittest.TestCase):
    """recall_history 工具：从 JSONL 原文中 BM25 检索。"""

    def setUp(self):
        """造临时工作区 + 一个 JSONL 会话文件。"""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

        # 造 .autocoding/sessions/ 目录和一个 JSONL 文件
        sessions_dir = self.root / ".autocoding" / "sessions"
        sessions_dir.mkdir(parents=True)

        messages = [
            {"role": "user", "content": "帮我把 MAX_RETRY 从 3 改成 5"},
            {"role": "assistant", "content": "好的，我来修改 config.py"},
            {"role": "tool", "content": "已将 MAX_RETRY = 3 修改为 MAX_RETRY = 5"},
            {"role": "user", "content": "再跑下测试看看"},
            {"role": "assistant", "content": "测试全部通过，3 passed"},
        ]
        jsonl_path = sessions_dir / "20260728-test.jsonl"
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg, ensure_ascii=False) + "\n")

        # 构造 CodingTools（需要 settings，但 recall_history 不依赖 LLM）
        from src.profiles.coding.tools import CodingTools
        self.tools = CodingTools(self.root, max_output_chars=5000)

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, **kwargs):
        return ToolCall(id="call_1", name="recall_history", arguments=kwargs)

    def test_search_english_keyword(self):
        """搜英文关键词 → 返回含该词的历史片段。"""
        result = self.tools.execute(self._call(query="MAX_RETRY"))
        self.assertFalse(result.error)
        # 应该找到第 0 轮和第 2 轮（都含 MAX_RETRY）
        self.assertIn("MAX_RETRY", result.content)
        self.assertIn("第0轮", result.content)

    def test_search_chinese_keyword(self):
        """搜中文关键词 → bigram 命中。"""
        result = self.tools.execute(self._call(query="测试通过"))
        self.assertFalse(result.error)
        # 应该找到第 4 轮（"测试全部通过"）
        self.assertIn("通过", result.content)

    def test_no_match_returns_friendly_message(self):
        """无匹配 → 返回友好提示，不崩。"""
        result = self.tools.execute(self._call(query="zzzznotexist"))
        self.assertFalse(result.error)
        self.assertIn("没找到", result.content)

    def test_empty_session_returns_friendly_message(self):
        """空会话（无 JSONL 文件）→ 返回友好提示，不崩。

        Spec: Out of Scope 里说只搜当前 session，所以先造一个空的 work area。
        """
        # 不调 setUp，直接造临时工作区但没有 sessions 目录
        tmp = tempfile.TemporaryDirectory()
        root = Path(tmp.name)
        from src.profiles.coding.tools import CodingTools
        tools = CodingTools(root, max_output_chars=5000)

        result = tools.execute(self._call(query="zzz"))
        self.assertFalse(result.error)
        self.assertIn("没找到", result.content)
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
