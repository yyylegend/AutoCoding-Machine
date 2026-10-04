"""ContextManager 测试。"""

import unittest

from auto_coding_machine.engine.context_manager import ContextManager, count_tokens


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

    def test_compact_preserves_latest_tool_batch_when_it_exceeds_limit(self):
        messages = [
            {"role": "user", "content": "查询四个订单"},
            {"role": "assistant", "content": "", "tool_calls": [
                {"id": f"q-{i}", "type": "function", "function": {
                    "name": "query_order", "arguments": '{"order_id":"A1001"}',
                }} for i in range(4)
            ]},
            *[{"role": "tool", "tool_call_id": f"q-{i}", "content": "已发货"}
              for i in range(4)],
        ]
        for manager in (ContextManager(max_messages=3),
                        ContextManager(max_messages=None, max_tokens=1)):
            with self.subTest(max_tokens=manager.max_tokens):
                result = manager.maybe_compact(messages)
                self.assertEqual(result[0]["role"], "assistant")
                self.assertEqual([m["tool_call_id"] for m in result[1:]],
                                 ["q-0", "q-1", "q-2", "q-3"])


if __name__ == "__main__":
    unittest.main()
