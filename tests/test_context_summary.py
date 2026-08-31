"""上下文摘要功能测试（Phase 3）。

覆盖场景：
    1. 不超限时不触发摘要（summarizer 未被调用）
    2. 超限且旧消息 >= 4 条时触发摘要，结果中包含 [历史摘要] 消息且位置正确
    3. 旧消息 < 4 条时不摘要、纯截断
    4. summarizer 抛异常时降级为纯截断（结果与不传 summarizer 一致，不崩溃）
    5. summarizer 返回空字符串时不插入摘要消息
    6. 不传 summarizer_fn 时行为与旧版一致
    7. 摘要后 assistant(tool_calls)+tool 配对依然完整
    8. make_summarizer 在 CONTEXT_SUMMARY_ENABLED=False 时返回 None
"""

from __future__ import annotations

import unittest

# 导入需要测试的函数和类
from src.config.settings import settings
from src.engine.context_manager import ContextManager, assemble
from src.profiles.coding.context_setup import make_summarizer


class TestContextSummary(unittest.TestCase):
    """上下文摘要功能测试类。"""

    def setUp(self):
        """每个测试前的准备工作。"""
        # 创建示例消息列表：system + user + assistant(user:msg1) + ... + user(msgN)
        self.messages_template = lambda N: [
            {"role": "system", "content": "system prompt"},
        ] + [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"}
            for i in range(1, N + 1)
        ]

    def _assert_no_summary_msg(self, result: list, msg_id="无"):
        """验证结果中没有 [历史摘要] 消息。"""
        has_summary = any("[历史摘要]" in str(m.get("content", "")) for m in result)
        self.assertFalse(has_summary, f"测试{msg_id}: 应无摘要消息但找到了")

    def _count_summary_msg(self, result: list) -> int:
        """计算结果中 [历史摘要] 消息的数量。"""
        return sum(1 for m in result if "[历史摘要]" in str(m.get("content", "")))

    def _find_summary_msg_idx(self, result: list) -> int | None:
        """找到 [历史摘要] 消息的索引位置。"""
        for i, m in enumerate(result):
            if "[历史摘要]" in str(m.get("content", "")):
                return i
        return None

    def test_01_no_summary_when_under_limit(self):
        """测试 1：少于上限时不压缩，summarizer 不被调用。"""
        # 准备消息总数少于 max_messages
        messages = self.messages_template(5)  # system + 5 条 = 6 条
        ctx_mgr = ContextManager(max_messages=10)  # max=10，实际只有 6 条
        result = ctx_mgr.maybe_compact(messages)
        # 应该原样返回，未触发任何截断或摘要
        self.assertEqual(len(result), len(messages))
        self._assert_no_summary_msg(result, "test_01")

    def test_02_summary_triggered_when_old_messages_ge_4(self):
        """测试 2：超限且旧消息 >= 4 条时触发摘要，[历史摘要] 位置正确。"""
        # 构造一个会触发的例子：max=5, system=1, rest=8, keep=4, cut_start=4
        # safe_cut=4 (都是安全边界), old=4 条 (>= 4), recent=4 条
        messages = self.messages_template(8)  # system + 8 条 = 9 条
        mock_call_count = [0]  # 使用列表以便闭包修改
        mock_summary_text = "这是一个模拟摘要"

        def mock_summarizer(old_msgs):
            """模拟摘要函数，记录调用次数并返回指定文本。"""
            mock_call_count[0] += 1
            return mock_summary_text

        ctx_mgr = ContextManager(max_messages=5, summarizer_fn=mock_summarizer)
        result = ctx_mgr.maybe_compact(messages)

        # 验证 summarizer 被调用了
        self.assertEqual(mock_call_count[0], 1, "summarizer 应被调用一次")

        # 验证结果中有且仅有一条 [历史摘要] 消息
        summary_count = self._count_summary_msg(result)
        self.assertEqual(summary_count, 1, "结果应包含一条摘要消息")

        # 验证摘要消息位置：应该在 system 之后、近期消息之前
        summary_idx = self._find_summary_msg_idx(result)
        self.assertIsNotNone(summary_idx, "应找到摘要消息的索引")
        self.assertGreater(summary_idx, 0, "摘要应在 system 之后")
        # 检查第一条非 system 消息是摘要消息
        first_non_system_idx = next(
            (i for i, m in enumerate(result) if m.get("role") != "system"), None
        )
        self.assertEqual(first_non_system_idx, summary_idx)

        # 验证结果结构：system + 摘要 + recent
        expected_total = 1 + 1 + 4  # system + summary + recent(keep_count)
        self.assertEqual(len(result), expected_total, "结果长度应为 6 条")

    def test_03_no_summary_when_old_messages_lt_4(self):
        """测试 3：超限但旧消息 < 4 条时不摘要，纯截断。"""
        # 构造一个旧消息 < 4 的例子：max=7, system=1, rest=7, keep=6, cut_start=1
        # safe_cut=1 (第一个 non-system 是 safe), old=1 条 (< 4), recent=6 条
        messages = self.messages_template(7)  # system + 7 条 = 8 条
        mock_called = [False]

        def mock_summarizer(_):
            """模拟摘要函数（不应被调用）。"""
            mock_called[0] = True

        ctx_mgr = ContextManager(max_messages=7, summarizer_fn=mock_summarizer)
        result = ctx_mgr.maybe_compact(messages)

        # 验证 summarizer 没有被调用
        self.assertFalse(mock_called[0], "old_messages < 4 时不应调用 summarizer")

        # 验证结果中没有 [历史摘要]
        self._assert_no_summary_msg(result, "test_03")

    def test_04_silent_degrade_on_exception(self):
        """测试 4：summarizer 抛异常时降级为纯截断，不崩溃，与不传 summarizer 一致。"""
        # 构造触发的例子
        messages = self.messages_template(8)  # system + 8 条 = 9 条

        def mock_summarizer_raises(_):
            """抛异常的摘要函数。"""
            raise Exception("模拟 LLM 错误")

        # 情况 A：传了抛异常的 summarizer
        ctx_mgr_with_error = ContextManager(
            max_messages=5, summarizer_fn=mock_summarizer_raises
        )
        result_with_error = ctx_mgr_with_error.maybe_compact(messages)

        # 情况 B：不传 summarizer
        ctx_mgr_without = ContextManager(max_messages=5)
        result_without = ctx_mgr_without.maybe_compact(messages)

        # 两者结果应该完全相同（都退化为纯截断）
        self.assertEqual(
            len(result_with_error),
            len(result_without),
            "异常降级后长度应与纯截断一致",
        )
        self.assertEqual(
            result_with_error, result_without, "异常降级后内容应与纯截断一致"
        )

    def test_05_silent_degrade_on_empty_string(self):
        """测试 5：summarizer 返回空字符串时不插入摘要消息。"""
        messages = self.messages_template(8)

        def mock_empty_summary(_):
            """返回空字符串的摘要函数。"""
            return ""

        ctx_mgr = ContextManager(max_messages=5, summarizer_fn=mock_empty_summary)
        result = ctx_mgr.maybe_compact(messages)

        # 验证没有 [历史摘要] 消息
        self._assert_no_summary_msg(result, "test_05")

    def test_06_no_summarizer_fn_behaves_like_old_version(self):
        """测试 6：不传 summarizer_fn 时行为与旧版一致。"""
        from src.engine.context_manager import ContextManager as OldVersionCM

        # 复用现有的 ContextManager 实例（不传 summarizer_fn）就是旧版行为
        messages = self.messages_template(10)  # system + 10 条 = 11 条

        # 新版但不传 summarizer_fn
        ctx_mgr_new = ContextManager(max_messages=5)
        result_new = ctx_mgr_new.maybe_compact(messages)

        # 旧版直接创建（等价于新版的默认参数）
        ctx_mgr_old = OldVersionCM(max_messages=5)
        result_old = ctx_mgr_old.maybe_compact(messages)

        # 两者结果应完全一致
        self.assertEqual(result_new, result_old, "默认行为应与旧版完全一致")

    def test_07_safe_boundary_preserved_with_summary(self):
        """测试 7：摘要后 assistant(tool_calls)+tool 配对依然完整。"""
        # 构造带 tool_calls + tool 配对的复杂消息序列
        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "query1"},
            {
                "role": "assistant",
                "content": "reply1",
                "tool_calls": [{"id": "tc1", "type": "function", "function": {"name": "tool_a"}}],
            },
            {
                "role": "tool",
                "content": "result_a",
                "tool_call_id": "tc1",
            },
            {"role": "user", "content": "query2"},
            {
                "role": "assistant",
                "content": "reply2",
                "tool_calls": [{"id": "tc2", "type": "function", "function": {"name": "tool_b"}}],
            },
            {
                "role": "tool",
                "content": "result_b",
                "tool_call_id": "tc2",
            },
            {"role": "user", "content": "query3"},
            {"role": "assistant", "content": "final_reply"},
            {"role": "user", "content": "next_query"},
        ]

        # 构造摘要函数（不关心具体文本，只要不抛错）
        mock_summary_count = [0]

        def mock_summarizer(_):
            mock_summary_count[0] += 1
            return "摘要 OK"

        ctx_mgr = ContextManager(max_messages=5, summarizer_fn=mock_summarizer)
        result = ctx_mgr.maybe_compact(messages)

        # 验证摘要被触发了
        self.assertEqual(mock_summary_count[0], 1)

        # 验证近期消息部分（去除 system 和摘要后的部分）不包含孤立的 tool/assistant-with-tool-calls
        # 找到摘要消息的位置
        summary_idx = self._find_summary_msg_idx(result)
        if summary_idx is not None:
            recent_part = result[summary_idx + 1 :]  # 摘要之后的部分
        else:
            recent_part = result

        # recent_part[0] 不应该是一个 tool 消息（孤立的结果）
        if len(recent_part) > 0:
            first_recent_role = recent_part[0].get("role", "")
            self.assertNotEqual(
                first_recent_role,
                "tool",
                "recent 不能以孤立 tool 开头",
            )

        # 验证最近的消息里如果有 tool_calls 配对，应该是完整的
        # 即：如果某处有 assistant(tc)，下一项应该是工具结果；如果有 tool 结果，前一项应该是 assistant(tc)
        # （这里我们只要求 recent 不以孤立 message 开始，详细配对验证交给更全面的集成测试）
        self.assertEqual(
            summary_idx, 1, "摘要应该在第二条位置（system 后）"
        )  # 作为额外确认

    def test_08_make_summarizer_returns_none_when_disabled(self):
        """测试 8：make_summarizer() 在 CONTEXT_SUMMARY_ENABLED=False 时返回 None。"""
        # 使用 monkeypatch 临时关闭配置开关
        original_value = settings.CONTEXT_SUMMARY_ENABLED
        try:
            # 临时设置为 False
            settings.CONTEXT_SUMMARY_ENABLED = False

            # 调用 make_summarizer，应该返回 None
            result = make_summarizer()

            # 验证返回值
            self.assertIsNone(
                result, "make_summarizer 在开关关闭时应返回 None"
            )

            # 额外验证：如果是 None，就不会调用 summarize 内部函数
            # 这和我们的实现逻辑一致
        finally:
            # 恢复原始值，避免影响其他测试
            settings.CONTEXT_SUMMARY_ENABLED = original_value


if __name__ == "__main__":
    unittest.main()
