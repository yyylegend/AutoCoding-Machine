"""Session 持久化测试。

覆盖四块：
1. SessionStore 单元测试     — append/load 往返、坏行跳过、懒创建、列表排序
2. MachineLoop 集成测试      — 假 model_fn 跑真循环，断言消息逐条落盘
                              （同时验证"助手回复不回流"的老 bug 已修复）
3. open_session 纯函数测试   — --resume 的三种取值（新开/最近/指定 id）
4. 接线测试                  — build_context_manager 滑动窗口 + 摘要配置齐全
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.engine import (
    AgentResponse,
    BudgetPolicy,
    CancellationToken,
    GuardManager,
    HookManager,
    MachineLoop,
    PermissionManager,
    SessionStore,
    ToolCall,
    latest_session_id,
    list_sessions,
    new_session_id,
    sessions_dir_for,
)


# ============================================================
# 1. SessionStore 单元测试
# ============================================================

class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_append_then_load_roundtrip(self):
        """append 三条消息，load 应原样读回（顺序一致）。"""
        store = SessionStore(self.dir, "s1")
        msgs = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好！有什么可以帮你？"},
            {"role": "user", "content": "读一下 cli.py"},
        ]
        for m in msgs:
            store.append(m)
        self.assertEqual(store.load(), msgs)

    def test_append_writes_one_line_per_message(self):
        """一条消息就是文件里的一行（JSONL 格式）。"""
        store = SessionStore(self.dir, "s2")
        store.append({"role": "user", "content": "a"})
        store.append({"role": "assistant", "content": "b"})
        lines = store.path.read_text(encoding="utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)

    def test_lazy_creation(self):
        """只 new 不 append 时不产生文件（没聊过天不留空文件）。"""
        store = SessionStore(self.dir, "s3")
        self.assertFalse(store.path.exists())
        self.assertEqual(store.load(), [])  # 文件不存在 → 空列表

    def test_load_skips_broken_line(self):
        """坏行（崩溃写了半截的 JSON）跳过，不报错、不影响好行。"""
        store = SessionStore(self.dir, "s4")
        store.append({"role": "user", "content": "第一条"})
        # 模拟崩溃：手动往文件里塞半截 JSON
        with open(store.path, "a", encoding="utf-8") as f:
            f.write('{"role": "assis')
        store2 = SessionStore(self.dir, "s4")
        history = store2.load()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["content"], "第一条")

    def test_chinese_saved_as_readable_text(self):
        """中文直接明文保存（ensure_ascii=False），文件人类可读。"""
        store = SessionStore(self.dir, "s5")
        store.append({"role": "user", "content": "帮我修个 bug"})
        raw = store.path.read_text(encoding="utf-8")
        self.assertIn("帮我修个 bug", raw)

    def test_list_sessions_title_and_order(self):
        """列表标题取首条 user 消息前 50 字；按 mtime 新的在前。"""
        old = SessionStore(self.dir, "old")
        old.append({"role": "user", "content": "旧会话的问题"})
        new = SessionStore(self.dir, "new")
        new.append({"role": "user", "content": "新会话的问题"})
        # 手动把 mtime 拉开差距（连续创建可能同一秒）
        os.utime(old.path, (1000, 1000))
        os.utime(new.path, (2000, 2000))

        sessions = list_sessions(self.dir)
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions[0]["id"], "new")  # 新的在前
        self.assertEqual(sessions[0]["title"], "新会话的问题")
        self.assertEqual(sessions[1]["id"], "old")

    def test_list_sessions_empty_dir(self):
        """目录不存在 → 空列表；latest → None。"""
        missing = self.dir / "不存在的子目录"
        self.assertEqual(list_sessions(missing), [])
        self.assertIsNone(latest_session_id(missing))

    def test_latest_session_id(self):
        """latest_session_id 返回 mtime 最新的那个。"""
        a = SessionStore(self.dir, "a")
        a.append({"role": "user", "content": "x"})
        b = SessionStore(self.dir, "b")
        b.append({"role": "user", "content": "y"})
        os.utime(a.path, (1000, 1000))
        os.utime(b.path, (2000, 2000))
        self.assertEqual(latest_session_id(self.dir), "b")

    def test_new_session_id_unique(self):
        """连续生成的 id 不重复（时间戳 + 随机串）。"""
        ids = {new_session_id() for _ in range(20)}
        self.assertEqual(len(ids), 20)

    def test_sessions_dir_for(self):
        """session 目录约定：<workspace>/.autocoding/sessions。"""
        d = sessions_dir_for(self.dir)
        self.assertEqual(d, self.dir / ".autocoding" / "sessions")


# ============================================================
# 2. MachineLoop + SessionStore 集成测试
# ============================================================

class TestMachineLoopWithSessionStore(unittest.TestCase):
    """假 model_fn 跑真 MachineLoop，验证消息逐条落盘。

    这同时验证了老 bug 的修复：以前助手回复和工具消息只写在
    临时 messages 上、从不回流；现在都进了 JSONL，谁也丢不了。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "test.txt").write_text("hello world", encoding="utf-8")

        # 用真工具（read_file 是 AUTO 权限，不会被拦）
        from src.profiles.coding.tools import CodingTools
        self.tools = CodingTools(self.root, max_output_chars=100)
        self.store = SessionStore(self.root / "sessions", "run-1")

    def tearDown(self):
        self.tmp.cleanup()

    def _run_loop(self, model_fn):
        loop = MachineLoop(
            model_fn=model_fn,
            tools=self.tools,
            permission=PermissionManager(),
            guard=GuardManager(),
            budget=BudgetPolicy(max_turns=10),
            final_verifier=lambda msgs, resp: resp.done,
            hooks=HookManager(),
            session_store=self.store,
        )
        return loop.run([{"role": "user", "content": "读文件"}], CancellationToken())

    def test_tool_turn_messages_written_to_jsonl(self):
        """一轮工具调用后，JSONL 里应有 assistant(tool_calls)、tool、最终 assistant。"""
        call_count = [0]

        def mock_model_fn(messages):
            call_count[0] += 1
            if call_count[0] == 1:
                return AgentResponse(
                    content="我先读文件",
                    tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "test.txt"})],
                )
            return AgentResponse(content="文件读完了", done=True)

        result = self._run_loop(mock_model_fn)
        self.assertEqual(result["status"], "success")

        saved = self.store.load()
        roles = [m["role"] for m in saved]
        # 注意：初始 user 消息是调用方（CLI/executor）负责写的，
        # loop 只写它自己产生的消息，所以这里没有 user
        self.assertEqual(roles, ["assistant", "tool", "assistant"])
        # 第一条 assistant 带 tool_calls
        self.assertTrue(saved[0].get("tool_calls"))
        # tool 消息配对正确
        self.assertEqual(saved[1]["tool_call_id"], "c1")
        # 最终回复也在（resume 后不丢最后一句）
        self.assertIn("文件读完了", saved[2]["content"])

    def test_need_input_reply_also_written(self):
        """模型只回文本（need_input）时，这句话也要落盘。"""
        def mock_model_fn(messages):
            return AgentResponse(content="你想先做哪个？", done=False)

        result = self._run_loop(mock_model_fn)
        self.assertEqual(result["status"], "need_input")
        saved = self.store.load()
        self.assertEqual(len(saved), 1)
        self.assertIn("你想先做哪个", saved[0]["content"])

    def test_no_store_behaves_as_before(self):
        """不传 session_store 时行为不变（不写任何文件）。"""
        loop = MachineLoop(
            model_fn=lambda msgs: AgentResponse(content="done", done=True),
            tools=self.tools,
            permission=PermissionManager(),
            guard=GuardManager(),
            budget=BudgetPolicy(max_turns=5),
            final_verifier=lambda msgs, resp: resp.done,
            hooks=HookManager(),
        )
        result = loop.run([], CancellationToken())
        self.assertEqual(result["status"], "success")
        self.assertFalse((self.root / "sessions").exists())


# ============================================================
# 3. open_session 纯函数测试（CLI 的 --resume 逻辑）
# ============================================================

class TestOpenSession(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_resume_starts_fresh(self):
        """resume=None → 新会话，history 为空，无报错。"""
        from src.engine.session_store import open_session
        store, history, error = open_session(self.dir, None)
        self.assertIsNone(error)
        self.assertEqual(history, [])
        self.assertIsNotNone(store.session_id)

    def test_resume_latest_picks_newest(self):
        """resume=""（--resume 不带值）→ 恢复 mtime 最新的会话。"""
        from src.engine.session_store import open_session
        a = SessionStore(self.dir, "a")
        a.append({"role": "user", "content": "旧"})
        b = SessionStore(self.dir, "b")
        b.append({"role": "user", "content": "新"})
        os.utime(a.path, (1000, 1000))
        os.utime(b.path, (2000, 2000))

        store, history, error = open_session(self.dir, "")
        self.assertIsNone(error)
        self.assertEqual(store.session_id, "b")
        self.assertEqual(history[0]["content"], "新")

    def test_resume_latest_with_no_sessions_errors(self):
        """resume="" 但一个会话都没有 → 给出清晰报错。"""
        from src.engine.session_store import open_session
        store, history, error = open_session(self.dir, "")
        self.assertIsNone(store)
        self.assertIsNotNone(error)

    def test_resume_specific_id(self):
        """resume=<id> → 恢复指定会话。"""
        from src.engine.session_store import open_session
        s = SessionStore(self.dir, "target")
        s.append({"role": "user", "content": "指定的会话"})
        store, history, error = open_session(self.dir, "target")
        self.assertIsNone(error)
        self.assertEqual(store.session_id, "target")
        self.assertEqual(len(history), 1)

    def test_resume_missing_id_errors(self):
        """resume=<不存在的 id> → 报错而不是静默新开。"""
        from src.engine.session_store import open_session
        store, history, error = open_session(self.dir, "ghost")
        self.assertIsNone(store)
        self.assertIn("ghost", error)


# ============================================================
# 4. 接线测试：ContextManager 配置齐全（滑动窗口 + 摘要）
# ============================================================

class TestContextManagerWiring(unittest.TestCase):
    """验证 build_context_manager 两个能力都接上了。

    以前 CLI 只有滑动窗口没摘要、executor 只有摘要没 token 预算，
    统一收编到 context_setup 后，这里锁住"不再缺一半"。
    """

    def setUp(self):
        from src.profiles.coding import context_setup
        self.context_setup = context_setup
        # 预填缓存，避免测试发真实网络请求
        context_setup._budget_cache = 99999

    def tearDown(self):
        self.context_setup._budget_cache = None

    def test_has_both_token_budget_and_summarizer(self):
        """摘要开关开启时：max_tokens 和 summarizer_fn 都不为空。"""
        from src.config.settings import settings
        original = settings.CONTEXT_SUMMARY_ENABLED
        settings.CONTEXT_SUMMARY_ENABLED = True
        try:
            cm = self.context_setup.build_context_manager(max_messages=20)
            self.assertEqual(cm.max_messages, 20)
            self.assertEqual(cm.max_tokens, 99999)      # 滑动窗口（token 维度）✓
            self.assertIsNotNone(cm.summarizer_fn)      # 历史摘要 ✓
        finally:
            settings.CONTEXT_SUMMARY_ENABLED = original

    def test_summary_disabled_falls_back_to_truncate(self):
        """摘要开关关闭时：summarizer_fn 为 None（纯截断），预算仍在。"""
        from src.config.settings import settings
        original = settings.CONTEXT_SUMMARY_ENABLED
        settings.CONTEXT_SUMMARY_ENABLED = False
        try:
            cm = self.context_setup.build_context_manager(max_messages=30)
            self.assertIsNone(cm.summarizer_fn)
            self.assertEqual(cm.max_tokens, 99999)
        finally:
            settings.CONTEXT_SUMMARY_ENABLED = original

    def test_explicit_budget_overrides_cache(self):
        """显式传 token_budget 时用传入值（CLI 展示用同一个数）。"""
        cm = self.context_setup.build_context_manager(max_messages=20, token_budget=12345)
        self.assertEqual(cm.max_tokens, 12345)


class TestContextBudgetResolution(unittest.TestCase):
    """上下文窗口解析与安全预算计算。"""

    def setUp(self):
        from src.config.settings import settings
        from src.profiles.coding import context_setup

        self.settings = settings
        self.context_setup = context_setup
        self.original_context_length = settings.CODING_CONTEXT_LENGTH
        self.original_max_output = settings.CODING_LLM_MAX_TOKENS
        context_setup._budget_cache = None

    def tearDown(self):
        self.settings.CODING_CONTEXT_LENGTH = self.original_context_length
        self.settings.CODING_LLM_MAX_TOKENS = self.original_max_output
        self.context_setup._budget_cache = None

    def test_explicit_context_length_has_highest_priority(self):
        from unittest.mock import patch

        self.settings.CODING_CONTEXT_LENGTH = 64000
        with patch.object(self.context_setup, "fetch_model_context_window") as fetch:
            length = self.context_setup.resolve_context_length()

        self.assertEqual(length, 64000)
        fetch.assert_not_called()

    def test_provider_context_length_is_used_when_not_configured(self):
        from unittest.mock import patch

        self.settings.CODING_CONTEXT_LENGTH = None
        with patch.object(self.context_setup, "fetch_model_context_window", return_value=100000):
            length = self.context_setup.resolve_context_length()

        self.assertEqual(length, 100000)

    def test_invalid_explicit_context_length_is_rejected(self):
        self.settings.CODING_CONTEXT_LENGTH = 0
        with self.assertRaisesRegex(ValueError, "必须是正整数"):
            self.context_setup.resolve_context_length()

    def test_default_context_length_is_last_fallback(self):
        from unittest.mock import patch

        self.settings.CODING_CONTEXT_LENGTH = None
        with patch.object(self.context_setup, "fetch_model_context_window", return_value=None):
            length = self.context_setup.resolve_context_length()

        self.assertEqual(length, self.context_setup.DEFAULT_CONTEXT_LENGTH)

    def test_budget_uses_ratio_for_large_window(self):
        budget = self.context_setup.calculate_token_budget(100000, 4096)
        self.assertEqual(budget, 80000)

    def test_budget_reserves_output_for_small_window(self):
        budget = self.context_setup.calculate_token_budget(8192, 4096)
        self.assertEqual(budget, 3072)

    def test_budget_rejects_window_smaller_than_reserved_space(self):
        with self.assertRaisesRegex(ValueError, "上下文窗口必须大于"):
            self.context_setup.calculate_token_budget(4096, 4096)

    def test_provider_accepts_common_context_length_field(self):
        from unittest.mock import patch

        from src.common.llm_client import fetch_model_context_window

        response = Mock(ok=True)
        response.json.return_value = {
            "data": [{"id": "demo-model", "context_length": "65536"}],
        }
        with patch("src.common.llm_client.requests.get", return_value=response):
            length = fetch_model_context_window(
                base_url="https://example.com/v1",
                api_key="test-key",
                model="demo-model",
            )

        self.assertEqual(length, 65536)


if __name__ == "__main__":
    unittest.main()
