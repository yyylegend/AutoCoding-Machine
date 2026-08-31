"""ContextManager 测试。"""

import unittest

from src.engine.context_manager import ContextManager, count_tokens


class TestContextManager(unittest.TestCase):
    def test_no_compact_if_under_limit(self):
        """少于上限时不压缩。"""
        ctx_mgr = ContextManager(max_messages=10)
        messages = [
            {"role": "system", "content": "你是助手"},
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好"},
        ]
        result = ctx_mgr.maybe_compact(messages)
        self.assertEqual(len(result), 3)

    def test_compact_keeps_system_and_recent(self):
        """超限时保留 system + 最近几条。"""
        ctx_mgr = ContextManager(max_messages=5)
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "reply1"},
            {"role": "user", "content": "msg2"},
            {"role": "assistant", "content": "reply2"},
            {"role": "user", "content": "msg3"},
            {"role": "assistant", "content": "reply3"},
            {"role": "user", "content": "msg4"},
        ]
        result = ctx_mgr.maybe_compact(messages)
        # 应该保留 system + 最近 4 条
        self.assertLessEqual(len(result), 5)
        self.assertEqual(result[0]["role"], "system")
        # 最后一条应该是 msg4
        self.assertIn("msg4", result[-1]["content"])

    def test_count_tokens_rough(self):
        """粗略 token 估算。"""
        messages = [
            {"role": "user", "content": "hello"},  # 5 字符 -> 约 2.5 token
        ]
        tokens = count_tokens(messages)
        self.assertGreater(tokens, 0)
        self.assertLess(tokens, 10)


if __name__ == "__main__":
    unittest.main()
