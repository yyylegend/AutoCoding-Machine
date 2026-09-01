"""记忆与压缩韧性测试（2026-08-31 改进）。

覆盖五块改动：
  1. 跨会话召回：多 session 搜索、最近 10 个护栏、坏行跳过、相邻命中合并、输出裁剪
  2. 记忆写入安全：并发不丢条目、写失败保住旧文件、动作级权限
  3. 摘要失败兜底：确定性摘录结构、字符上限、失败冷却、force 绕过
  4. 上下文超限：领域异常识别（含防误伤普通 400）、强制压缩后只重试一次
  5. SessionStore 不再有覆盖历史的方法

风格说明：用例按"改动的哪一块"分组，测试名即断言的行为，
方便以后改代码时一眼看出是哪条约定被破坏了。
"""

import json
import os
import tempfile
import threading
import time
from pathlib import Path

import pytest
import requests
from rich.console import Console

from src.common import llm_client
from src.common.llm_client import ContextLengthExceededError
from src.engine.contracts import (
    AgentResponse,
    BudgetPolicy,
    CancellationToken,
    PermissionDecision,
    ToolCall,
)
from src.engine.context_manager import EXCERPT_MAX_CHARS, ContextManager
from src.engine.guard_manager import GuardManager
from src.engine.hook_manager import HookManager
from src.engine.machine_loop import MachineLoop
from src.engine.memory_manager import MemoryManager
from src.engine.permission_manager import PermissionManager
from src.engine.session_store import SessionStore, sessions_dir_for
from src.profiles.coding import llm_adapter as adapter_module
from src.profiles.coding.llm_adapter import StreamingAdapter
from src.profiles.coding.tools import CodingTools


# ============================================================
#  公共小工具
# ============================================================

def _sessions_dir(tmp_path) -> Path:
    """造出工作区的 sessions 目录（CodingTools 会按同样规则去找它）。"""
    directory = sessions_dir_for(tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _write_session(sessions_dir, session_id, messages, mtime) -> Path:
    """写一个 JSONL session 文件，并指定修改时间。

    指定 mtime 是为了在测试里精确控制"哪个 session 更新"，
    不依赖文件创建的先后顺序（有些文件系统 mtime 精度不够）。
    """
    path = sessions_dir / (session_id + ".jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    os.utime(path, (mtime, mtime))
    return path


def _recall(tools, query):
    """调一次 recall_history，返回 ToolResult。"""
    call = ToolCall(id="call_1", name="recall_history", arguments={"query": query})
    return tools.execute(call)


def _memory_call(action, target="memory", content=None, old_text=None):
    """构造一个 memory 工具的 ToolCall。"""
    args = {"action": action, "target": target}
    if content is not None:
        args["content"] = content
    if old_text is not None:
        args["old_text"] = old_text
    return ToolCall(id="call_1", name="memory", arguments=args)


def _dialog_messages(count, prefix="msg"):
    """造 system + count 条对话消息，供压缩测试使用。"""
    messages = [{"role": "system", "content": "system prompt"}]
    for i in range(1, count + 1):
        role = "user" if i % 2 == 0 else "assistant"
        messages.append({"role": role, "content": prefix + str(i)})
    return messages


# ============================================================
#  1. 跨会话召回
# ============================================================

class TestCrossSessionRecall:
    """recall_history 的跨会话检索行为。"""

    def test_search_finds_older_session(self, tmp_path):
        """关键词只出现在旧 session 里，也应该能被搜到。"""
        sessions_dir = _sessions_dir(tmp_path)
        base = time.time()
        _write_session(sessions_dir, "s-old",
                       [{"role": "user", "content": "旧会话里提到 MAX_RETRY 配置"}],
                       base - 3000)
        _write_session(sessions_dir, "s-new",
                       [{"role": "user", "content": "新会话在聊别的事"}],
                       base - 1000)

        tools = CodingTools(tmp_path, max_output_chars=5000)
        result = _recall(tools, "MAX_RETRY")

        assert not result.error
        assert "MAX_RETRY" in result.content
        assert "s-old" in result.content, "命中应标注来自哪个 session"

    def test_hit_includes_session_id_and_file_time(self, tmp_path):
        """每条命中都要带 session ID 和文件时间，方便模型判断新旧。"""
        sessions_dir = _sessions_dir(tmp_path)
        _write_session(sessions_dir, "s-1",
                       [{"role": "user", "content": "关键词 ALPHA"}], time.time() - 100)

        tools = CodingTools(tmp_path, max_output_chars=5000)
        result = _recall(tools, "ALPHA")

        assert "session s-1" in result.content
        # 时间是 %Y-%m-%d %H:%M 格式，用年份和冒号做粗略校验
        assert str(time.localtime().tm_year) in result.content
        assert ":" in result.content

    def test_newer_session_first_when_scores_equal(self, tmp_path):
        """BM25 同分时，较新的 session 排在前面。"""
        sessions_dir = _sessions_dir(tmp_path)
        base = time.time()
        same = [{"role": "user", "content": "同分关键词 ZEBRA"}]
        _write_session(sessions_dir, "s-old", same, base - 3000)
        _write_session(sessions_dir, "s-new", same, base - 1000)

        tools = CodingTools(tmp_path, max_output_chars=5000)
        result = _recall(tools, "ZEBRA")

        new_pos = result.content.find("s-new")
        old_pos = result.content.find("s-old")
        assert new_pos >= 0 and old_pos >= 0, "两个 session 都应命中"
        assert new_pos < old_pos, "同分时较新的 session 应排在前面"

    def test_only_recent_ten_sessions_are_scanned(self, tmp_path):
        """只扫最近 10 个 session：第 11 个往后的内容搜不到（成本护栏）。"""
        sessions_dir = _sessions_dir(tmp_path)
        base = time.time()
        for i in range(12):
            content = "最旧会话的关键词 DEEPOLD" if i == 0 else "普通内容 %d" % i
            _write_session(sessions_dir, "s%02d" % i,
                           [{"role": "user", "content": content}],
                           base - (12 - i) * 100)

        tools = CodingTools(tmp_path, max_output_chars=5000)
        result = _recall(tools, "DEEPOLD")

        # 注意：搜不到时工具会把查询词回显在提示里，所以不能断言关键词不出现，
        # 要断言"没找到"（说明 s00 根本没进语料）
        assert "没找到" in result.content, "超出 10 个上限的旧 session 不应被扫描"

    def test_corrupt_jsonl_lines_are_skipped(self, tmp_path):
        """坏行（半截 JSON）跳过，不影响其他行的检索。"""
        sessions_dir = _sessions_dir(tmp_path)
        path = _write_session(
            sessions_dir, "s-1",
            [{"role": "user", "content": "正常消息含 GOODKEY"}],
            time.time() - 100,
        )
        with open(path, "a", encoding="utf-8") as f:
            f.write("{这行是半截JSON\n")

        tools = CodingTools(tmp_path, max_output_chars=5000)
        result = _recall(tools, "GOODKEY")

        assert not result.error
        assert "GOODKEY" in result.content

    def test_adjacent_hits_are_merged_without_duplicates(self, tmp_path):
        """相邻两行都命中时合并成一条，且同一行不重复输出。"""
        sessions_dir = _sessions_dir(tmp_path)
        _write_session(sessions_dir, "s-1", [
            {"role": "user", "content": "MERGEKEY 第一句"},
            {"role": "assistant", "content": "MERGEKEY 紧接着第二句"},
            {"role": "user", "content": "无关的第三句"},
        ], time.time() - 100)

        tools = CodingTools(tmp_path, max_output_chars=5000)
        result = _recall(tools, "MERGEKEY")

        # 相邻命中合并时，第 0 轮和第 1 轮各应只出现一次（不能重复渲染）
        assert result.content.count("第0轮") == 1
        assert result.content.count("第1轮") == 1

    def test_output_is_clipped_to_max_output_chars(self, tmp_path):
        """输出统一过 clip_text，不能超过 max_output_chars。"""
        sessions_dir = _sessions_dir(tmp_path)
        _write_session(sessions_dir, "s-1", [
            {"role": "user", "content": "BIGKEY " + ("很长的内容" * 400)},
        ], time.time() - 100)

        tools = CodingTools(tmp_path, max_output_chars=300)
        result = _recall(tools, "BIGKEY")

        assert len(result.content) <= 300
        assert "截断" in result.content

    def test_no_sessions_dir_returns_friendly_message(self, tmp_path):
        """工作区还没有任何会话时，返回友好提示而不是报错。"""
        tools = CodingTools(tmp_path, max_output_chars=5000)
        result = _recall(tools, "ANYTHING")
        assert not result.error
        assert "没找到" in result.content or "没有" in result.content


# ============================================================
#  2. 记忆写入安全
# ============================================================

class TestMemoryWriteSafety:
    """MemoryManager 的并发安全与原子写入。"""

    def _manager(self, tmp_path, memory_limit=5000):
        return MemoryManager(
            memory_path=tmp_path / ".autocoding" / "MEMORY.md",
            user_path=tmp_path / "fake_home" / "USER.md",
            memory_limit=memory_limit,
            user_limit=2000,
        )

    def test_concurrent_adds_do_not_lose_entries(self, tmp_path):
        """两个线程同时写记忆，一条都不能丢。"""
        manager = self._manager(tmp_path)
        failures = []

        def worker(tag):
            for i in range(10):
                result = manager.add("memory", tag + "-entry-" + str(i))
                if not result["ok"]:
                    failures.append(result["message"])

        t1 = threading.Thread(target=worker, args=("thread1",))
        t2 = threading.Thread(target=worker, args=("thread2",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert failures == []
        text = manager.load("memory")
        for tag in ("thread1", "thread2"):
            for i in range(10):
                assert tag + "-entry-" + str(i) in text, "并发写入丢失了条目"

    def test_write_failure_keeps_old_file_intact(self, tmp_path, monkeypatch):
        """原子替换失败时：旧文件一个字节都不能坏，临时文件要清掉。"""
        manager = self._manager(tmp_path)
        manager.add("memory", "原始条目")
        memory_path = tmp_path / ".autocoding" / "MEMORY.md"
        original_text = memory_path.read_text(encoding="utf-8")

        def _boom(self, target):
            raise OSError("模拟写入失败")

        monkeypatch.setattr(Path, "replace", _boom)

        with pytest.raises(OSError):
            manager.add("memory", "这条应该写不进去")

        assert memory_path.read_text(encoding="utf-8") == original_text, "旧文件被破坏了"
        assert list(memory_path.parent.glob("*.tmp")) == [], "临时文件没清理干净"

    def test_read_failure_does_not_overwrite_old_memory(self, tmp_path, monkeypatch):
        """写操作读取旧文件失败时必须中止，不能把旧内容当成空文件覆盖。"""
        manager = self._manager(tmp_path)
        manager.add("memory", "原始条目")
        memory_path = tmp_path / ".autocoding" / "MEMORY.md"
        original_text = memory_path.read_text(encoding="utf-8")
        original_read_text = Path.read_text

        def _fail_memory_read(path, *args, **kwargs):
            if path == memory_path:
                raise OSError("模拟瞬时读取失败")
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(Path, "read_text", _fail_memory_read)
        with pytest.raises(OSError):
            manager.add("memory", "不应覆盖旧文件")

        monkeypatch.setattr(Path, "read_text", original_read_text)
        assert memory_path.read_text(encoding="utf-8") == original_text

    def test_add_is_auto_replace_and_remove_ask(self, tmp_path):
        """动作级权限：add 自动放行，replace / remove 需要用户确认。"""
        tools = CodingTools(tmp_path)
        permission = PermissionManager(tool_manager=tools.get_manager())

        assert permission.check(_memory_call("add", content="x")) == PermissionDecision.AUTO
        assert permission.check(
            _memory_call("replace", content="new", old_text="old")
        ) == PermissionDecision.ASK
        assert permission.check(
            _memory_call("remove", old_text="old")
        ) == PermissionDecision.ASK

    def test_auto_approve_turns_ask_into_auto(self, tmp_path):
        """无人值守模式（评测）下，ASK 放行成 AUTO。"""
        tools = CodingTools(tmp_path)
        permission = PermissionManager(tool_manager=tools.get_manager(),
                                       auto_approve=True)
        assert permission.check(_memory_call("remove", old_text="x")) == PermissionDecision.AUTO

    def test_remove_is_unchanged_on_deny_and_executes_after_approval(self, tmp_path):
        """拒绝时不执行；批准后执行同一个 pending remove 调用。"""
        tools = CodingTools(tmp_path)
        manager = tools.get_manager()
        permission = PermissionManager(tool_manager=manager)
        add_call = _memory_call("add", content="待确认删除的条目")
        remove_call = _memory_call("remove", old_text="待确认删除")

        assert not tools.execute(add_call).error
        memory_path = tmp_path / ".autocoding" / "MEMORY.md"
        before = memory_path.read_text(encoding="utf-8")

        # deny：权限检查只返回 ASK，调用方拒绝后不执行工具，文件保持原样。
        assert permission.check(remove_call) == PermissionDecision.ASK
        assert memory_path.read_text(encoding="utf-8") == before

        # approve：调用方明确批准后才执行 pending tool call。
        approved_result = tools.execute(remove_call)
        assert not approved_result.error
        assert "待确认删除" not in memory_path.read_text(encoding="utf-8")

    def test_write_error_becomes_tool_error_not_crash(self, tmp_path, monkeypatch):
        """写入抛异常时转成工具错误返回，不能让整个循环崩掉。"""
        tools = CodingTools(tmp_path)

        def _boom(self, target):
            raise OSError("磁盘满了")

        monkeypatch.setattr(Path, "replace", _boom)
        result = tools.execute(_memory_call("add", content="写不进去的一条"))

        assert result.error
        assert "失败" in result.content


# ============================================================
#  3. 摘要失败的确定性兜底
# ============================================================

class TestSummaryFallback:
    """摘要失败 → 确定性摘录，以及失败冷却。"""

    def test_failure_is_cooled_down_not_retried(self):
        """摘要失败后进入冷却：同一批旧消息不会每轮都重试 LLM。"""
        calls = []

        def broken_summarizer(_):
            calls.append(1)
            raise Exception("模拟 LLM 超时")

        ctx = ContextManager(max_messages=5, summarizer_fn=broken_summarizer)
        messages = _dialog_messages(8)

        ctx.maybe_compact(messages)
        assert len(calls) == 1

        ctx.maybe_compact(messages)  # 同一批旧消息，仍在冷却期内
        assert len(calls) == 1, "冷却期内不应重复请求模型"
        assert ctx.last_compaction_mode == "excerpt"

    @pytest.mark.parametrize(
        "failure",
        [requests.Timeout("摘要超时"), requests.HTTPError("摘要 HTTP 失败")],
    )
    def test_transport_failures_use_excerpt(self, failure):
        """Timeout 与 HTTPError 都应降级为摘录，不中断任务。"""
        def broken_summarizer(_):
            raise failure

        ctx = ContextManager(max_messages=5, summarizer_fn=broken_summarizer)
        result = ctx.maybe_compact(_dialog_messages(8))

        assert ctx.last_compaction_mode == "excerpt"
        assert str(failure) in ctx.last_compaction_error
        assert "仅作历史参考" in result[1]["content"]

    def test_force_bypasses_cooldown(self):
        """手动 /compact（force=True）绕过冷却，允许立即重试。

        注意：force 走 token 预算切分路径，必须配 max_tokens 才有效果。
        """
        calls = []

        def broken_summarizer(_):
            calls.append(1)
            raise Exception("模拟 LLM 超时")

        ctx = ContextManager(max_messages=None, max_tokens=60,
                             summarizer_fn=broken_summarizer)
        messages = _dialog_messages(8, prefix="这是一条用来占 token 的测试消息")

        ctx.maybe_compact(messages)
        first_round = len(calls)

        ctx.maybe_compact(messages, force=True)
        assert len(calls) > first_round, "force=True 应绕过冷却重新尝试摘要"

    def test_excerpt_contains_goal_errors_and_files(self):
        """摘录应包含：免责声明、原始目标、报错现场、涉及文件。"""
        def broken_summarizer(_):
            raise Exception("模拟 LLM 错误")

        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "帮我把 config.py 里的超时改成 30 秒"},
            {"role": "assistant", "content": "好，我先读文件",
             "tool_calls": [{"id": "t1"}]},
            {"role": "tool", "content": "Error: 找不到 src/config.py"},
            {"role": "user", "content": "那就先新建文件"},
            {"role": "tool", "content": "报错: 没有写权限 utils.py"},
            {"role": "assistant", "content": "改好了 settings.yaml"},
            {"role": "user", "content": "跑一下测试"},
        ]

        # max_messages=4：旧消息 5 条（>= 4 才会触发摘要），近期保留 2 条
        ctx = ContextManager(max_messages=4, summarizer_fn=broken_summarizer)
        result = ctx.maybe_compact(messages)

        excerpt = result[1]["content"]
        assert "仅作历史参考" in excerpt, "摘录开头必须有免责声明"
        assert "【原始目标】" in excerpt
        assert "config.py" in excerpt
        assert "报错" in excerpt or "Error" in excerpt, "应保留报错现场"
        assert ctx.last_compaction_mode == "excerpt"

    def test_excerpt_respects_char_cap(self):
        """摘录总长不超过 EXCERPT_MAX_CHARS。"""
        def broken_summarizer(_):
            raise Exception("模拟 LLM 错误")

        messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "超长目标 " + ("内容" * 3000)},
            {"role": "assistant", "content": "回复 " + ("内容" * 1000)},
            {"role": "tool", "content": "Error: " + ("堆栈" * 1000)},
            {"role": "user", "content": "继续 " + ("内容" * 500)},
            {"role": "assistant", "content": "好的 " + ("内容" * 500)},
            {"role": "user", "content": "再来一轮"},
            {"role": "assistant", "content": "收尾"},
        ]

        # max_messages=4：近��保留 3 条，旧消息 4 条（>= 4 触发摘要）
        ctx = ContextManager(max_messages=4, summarizer_fn=broken_summarizer)
        result = ctx.maybe_compact(messages)

        excerpt = result[1]["content"]
        assert "仅作历史参考" in excerpt, "这条应该是摘录而不是普通消息"
        # content 里还有 "[历史摘要] " 这个前缀，给一点余量
        assert len(excerpt) <= EXCERPT_MAX_CHARS + 20

    def test_excerpt_keeps_tool_pairing_intact(self):
        """降级为摘录后，保留下来的近期消息仍不能切断 tool 配对。"""
        def broken_summarizer(_):
            raise Exception("模拟 LLM 错误")

        messages = [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "query1"},
            {"role": "assistant", "content": "reply1",
             "tool_calls": [{"id": "tc1"}]},
            {"role": "tool", "content": "result_a", "tool_call_id": "tc1"},
            {"role": "user", "content": "query2"},
            {"role": "assistant", "content": "reply2",
             "tool_calls": [{"id": "tc2"}]},
            {"role": "tool", "content": "result_b", "tool_call_id": "tc2"},
            {"role": "user", "content": "query3"},
        ]

        ctx = ContextManager(max_messages=5, summarizer_fn=broken_summarizer)
        result = ctx.maybe_compact(messages)

        # 摘录消息之后的第一条不能是孤立的 tool 结果
        recent = result[2:]
        assert recent[0].get("role") != "tool", "recent 不能以孤立 tool 开头"

    def test_compaction_never_rewrites_session_jsonl(self, tmp_path):
        """压缩和摘录只改进程内视图，原始 JSONL 必须逐字节不变。"""
        store = SessionStore(_sessions_dir(tmp_path), "raw-history")
        messages = _dialog_messages(8)
        for message in messages[1:]:
            store.append(message)
        before = store.path.read_bytes()

        def broken_summarizer(_):
            raise requests.Timeout("摘要超时")

        ctx = ContextManager(max_messages=5, summarizer_fn=broken_summarizer)
        compacted = ctx.maybe_compact(messages)

        assert len(compacted) < len(messages)
        assert store.path.read_bytes() == before


# ============================================================
#  4. 上下文超限识别与单次重试
# ============================================================

class _FakeResponse:
    """假的 requests 响应对象，用来模拟 LLM 服务返回错误。"""

    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text
        self.ok = 200 <= status_code < 300


class TestContextLengthDetection:
    """llm_client 对上下文超限的识别（含防误伤普通 400）。"""

    def test_400_with_context_keyword_raises_domain_error(self, monkeypatch):
        """400 + context_length_exceeded → 领域异常（可自动恢复）。"""
        monkeypatch.setattr(
            llm_client.requests, "post",
            lambda *a, **k: _FakeResponse(400, '{"code":"context_length_exceeded"}'),
        )
        with pytest.raises(ContextLengthExceededError):
            llm_client.chat([{"role": "user", "content": "hi"}])

    def test_413_with_vllm_style_message_raises_domain_error(self, monkeypatch):
        """413 + maximum context length（vLLM 风格）→ 领域异常。"""
        monkeypatch.setattr(
            llm_client.requests, "post",
            lambda *a, **k: _FakeResponse(413, "maximum context length is 8192"),
        )
        with pytest.raises(ContextLengthExceededError):
            llm_client.chat([{"role": "user", "content": "hi"}])

    def test_plain_400_without_keyword_stays_http_error(self, monkeypatch):
        """普通 400（参数错 / JSON 解析失败）不能被误判成上下文超限。"""
        monkeypatch.setattr(
            llm_client.requests, "post",
            lambda *a, **k: _FakeResponse(400, "invalid tool json"),
        )
        with pytest.raises(requests.HTTPError):
            llm_client.chat([{"role": "user", "content": "hi"}])

    def test_streaming_adapter_propagates_context_overflow(self, monkeypatch):
        """流式超限必须直接交给 MachineLoop，不能拿原消息做非流式重发。"""
        adapter = StreamingAdapter([], Console(), {})
        plain_calls = []

        def _overflow(_messages, _buffer, **_kwargs):
            raise ContextLengthExceededError("模拟流式超限")

        def _plain_call(*args, **kwargs):
            plain_calls.append(1)
            return {"content": "不应调用", "tool_calls": [], "usage": {}}

        monkeypatch.setattr(adapter, "_call_streaming", _overflow)
        monkeypatch.setattr(adapter_module, "chat", _plain_call)

        with pytest.raises(ContextLengthExceededError):
            adapter.call([{"role": "user", "content": "hi"}])
        assert plain_calls == [], "超限后不能先用未压缩消息做非流式重发"


class _FakeContextManager:
    """假的压缩器：可控地"压缩掉"指定条数的消息。

    只实现 MachineLoop 会用到的两个接口：maybe_compact() 和诊断属性。
    """

    def __init__(self, drop=1):
        self.drop = drop
        self.last_compaction_mode = "truncated"
        self.force_calls = []

    def maybe_compact(self, messages, force=False):
        self.force_calls.append(force)
        if self.drop <= 0 or len(messages) <= 1:
            return messages
        return messages[:-self.drop]


class TestLoopContextOverflowRecovery:
    """MachineLoop 遇到上下文超限时的恢复策略（只重试一次）。"""

    def _build_loop(self, tmp_path, model_fn, context_manager, hooks=None):
        return MachineLoop(
            model_fn=model_fn,
            tools=CodingTools(tmp_path, max_output_chars=500),
            permission=PermissionManager(),
            guard=GuardManager(),
            budget=BudgetPolicy(max_turns=5),
            final_verifier=lambda messages, response: True,
            hooks=hooks or HookManager(),
            context_manager=context_manager,
        )

    def test_compacts_once_and_retries(self, tmp_path):
        """第一次超限 → 强制压缩 → 重试一次 → 成功。"""
        calls = []

        def model_fn(messages):
            calls.append(1)
            if len(calls) == 1:
                raise ContextLengthExceededError("模拟超限")
            return AgentResponse(content="好了", tool_calls=[], done=True)

        warnings = []
        hooks = HookManager()
        hooks.on("compaction_fallback", lambda **kw: warnings.append(kw))
        loop = self._build_loop(
            tmp_path, model_fn, _FakeContextManager(drop=1), hooks=hooks
        )
        result = loop.run(_dialog_messages(4), CancellationToken())

        assert result["status"] == "success"
        assert len(calls) == 2, "应只重试一次"
        assert len(warnings) == 1
        assert warnings[0]["kind"] == "context_overflow_retry"

    def test_fails_when_compaction_makes_no_progress(self, tmp_path):
        """压缩没减少内容 → 明确失败，不重试、不循环。"""
        calls = []

        def model_fn(messages):
            calls.append(1)
            raise ContextLengthExceededError("模拟超限")

        loop = self._build_loop(tmp_path, model_fn, _FakeContextManager(drop=0))
        result = loop.run(_dialog_messages(4), CancellationToken())

        assert result["status"] == "failed"
        assert "无法自动恢复" in result["error"]
        assert len(calls) == 1, "压缩无进展时不应重试模型"

    def test_fails_when_second_call_still_overflows(self, tmp_path):
        """压缩有进展但重试仍超限 → 明确失败并给出下一步建议。"""
        calls = []

        def model_fn(messages):
            calls.append(1)
            raise ContextLengthExceededError("模拟超限")

        loop = self._build_loop(tmp_path, model_fn, _FakeContextManager(drop=1))
        result = loop.run(_dialog_messages(4), CancellationToken())

        assert result["status"] == "failed"
        assert "/compact" in result["error"]
        assert len(calls) == 2, "最多重试一次"

    def test_fails_without_context_manager(self, tmp_path):
        """没配压缩器且上下文超限 → 明确失败（无从恢复）。"""

        def model_fn(messages):
            raise ContextLengthExceededError("模拟超限")

        loop = self._build_loop(tmp_path, model_fn, None)
        result = loop.run(_dialog_messages(4), CancellationToken())

        assert result["status"] == "failed"
        assert "未配置压缩器" in result["error"]


# ============================================================
#  5. SessionStore 不再有覆盖历史的方法
# ============================================================

def test_session_store_has_no_overwrite_method():
    """JSONL 只增不减是结构性保证：代码里不能存在覆盖历史的方法。"""
    assert not hasattr(SessionStore, "overwrite")
