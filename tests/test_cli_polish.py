"""CLI 体验增强测试（TUI P0 三件套）。

覆盖两块：
1. SlashCompleter              - 斜杠命令菜单 + /skill、/resume 参数补全
2. repair_dangling_tool_results - Ctrl+C 中断后修复悬空的 tool_calls

"""

import tempfile
import unittest
from pathlib import Path

from prompt_toolkit.document import Document

from src.engine import SessionStore
from src.engine import repair_dangling_tool_results
from src.profiles.coding.cli_input import SlashCompleter


def _make_completer(sessions=None):
    """造一个测试用补全器（技能 2 个，会话按需注入）。"""
    skills = [
        {"name": "tdd", "description": "测试驱动开发"},
        {"name": "handoff", "description": "交接文档生成"},
    ]
    return SlashCompleter(skills, lambda: sessions or [])


# ============================================================
# 1. SlashCompleter：命令菜单 + 参数补全
# ============================================================

class TestSlashCompleter(unittest.TestCase):

    def test_slash_lists_all_commands(self):
        """输入 / 应弹出全部命令菜单。"""
        completer = _make_completer()
        names = [c.text for c in completer.get_completions(Document("/"), None)]
        self.assertIn("/help", names)
        self.assertIn("/skills", names)
        self.assertIn("/skill ", names)    # 带参数命令带尾随空格
        self.assertIn("/resume ", names)
        self.assertIn("/quit", names)

    def test_partial_command_matches(self):
        """输入 /sk 同时匹配 /skill（带空格）和 /skills。"""
        completer = _make_completer()
        names = [c.text for c in completer.get_completions(Document("/sk"), None)]
        self.assertIn("/skill ", names)
        self.assertIn("/skills", names)

    def test_command_menu_has_description_meta(self):
        """命令菜单右边应显示一句话说明。"""
        completer = _make_completer()
        completions = list(completer.get_completions(Document("/help"), None))
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0].text, "/help")
        self.assertTrue(completions[0].display_meta_text)

    def test_skill_arg_completion(self):
        """/skill t 应补出 tdd（替换最后一个字符片段）。"""
        completer = _make_completer()
        completions = list(completer.get_completions(Document("/skill t"), None))
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0].text, "tdd")
        self.assertEqual(completions[0].start_position, -1)

    def test_skill_completion_shows_description(self):
        """技能补全的 meta 应显示技能描述。"""
        completer = _make_completer()
        completions = list(completer.get_completions(Document("/skill t"), None))
        self.assertEqual(completions[0].display_meta_text, "测试驱动开发")

    def test_resume_arg_completion(self):
        """/resume <片段> 应补会话 id，meta 显示会话标题。"""
        sessions = [{"id": "20260801-120000-aaa111", "title": "修个bug", "mtime": 1.0}]
        completer = _make_completer(sessions=sessions)
        completions = list(completer.get_completions(Document("/resume 2026"), None))
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0].text, "20260801-120000-aaa111")
        self.assertEqual(completions[0].display_meta_text, "修个bug")

    def test_resume_completion_cached(self):
        """会话清单带 TTL 缓存：查询函数只被调用一次。"""
        calls = []

        def get_sessions():
            calls.append(1)
            return [{"id": "s1", "title": "t", "mtime": 1.0}]

        completer = SlashCompleter([], get_sessions)
        list(completer.get_completions(Document("/resume "), None))
        list(completer.get_completions(Document("/resume s"), None))
        self.assertEqual(len(calls), 1)

    def test_plain_text_no_completions(self):
        """普通聊天文字不应弹菜单。"""
        completer = _make_completer()
        completions = list(completer.get_completions(Document("帮我看看代码"), None))
        self.assertEqual(completions, [])

    def test_command_with_other_args_no_menu(self):
        """命令后已跟其它文字（非 /skill、/resume）不应补全。"""
        completer = _make_completer()
        completions = list(completer.get_completions(Document("/help me"), None))
        self.assertEqual(completions, [])


# ============================================================
# 2. _format_tool_args：工具参数摘要
# ============================================================

class TestRepairDanglingToolResults(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _assistant_tool_call(call_id):
        """造一条带 tool_calls 的 assistant 消息（OpenAI 格式）。"""
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": call_id,
                "type": "function",
                "function": {"name": "read_file", "arguments": "{}"},
            }],
        }

    def test_repairs_missing_tool_result(self):
        """悬空的 tool_calls 应补一条 cancelled 回执。"""
        store = SessionStore(self.dir, "s1")
        store.append({"role": "user", "content": "读一下"})
        store.append(self._assistant_tool_call("call_1"))
        # 模拟 Ctrl+C 打断：没有 tool 结果

        repaired = repair_dangling_tool_results(store)
        self.assertEqual(repaired, 1)

        history = store.load()
        self.assertEqual(history[-1]["role"], "tool")
        self.assertEqual(history[-1]["tool_call_id"], "call_1")

    def test_no_repair_when_paired(self):
        """配对完整的历史不应被改动（返回 0）。"""
        store = SessionStore(self.dir, "s2")
        store.append(self._assistant_tool_call("call_1"))
        store.append({"role": "tool", "tool_call_id": "call_1", "content": "ok"})
        self.assertEqual(repair_dangling_tool_results(store), 0)
        self.assertEqual(len(store.load()), 2)

    def test_repairs_only_missing_one(self):
        """两个 tool_calls 只断了一个，只补那一个。"""
        assistant = self._assistant_tool_call("call_1")
        assistant["tool_calls"].append({
            "id": "call_2",
            "type": "function",
            "function": {"name": "write_file", "arguments": "{}"},
        })
        store = SessionStore(self.dir, "s3")
        store.append(assistant)
        store.append({"role": "tool", "tool_call_id": "call_1", "content": "ok"})
        # call_2 悬空

        repaired = repair_dangling_tool_results(store)
        self.assertEqual(repaired, 1)

        history = store.load()
        self.assertEqual(history[-1]["tool_call_id"], "call_2")

    def test_repair_is_idempotent(self):
        """修复后再跑一次应返回 0（不重复补）。"""
        store = SessionStore(self.dir, "s4")
        store.append(self._assistant_tool_call("call_1"))
        self.assertEqual(repair_dangling_tool_results(store), 1)
        self.assertEqual(repair_dangling_tool_results(store), 0)


if __name__ == "__main__":
    unittest.main()
