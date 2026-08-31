"""MachineLoop 测试（用 mock LLM）。

目标：验证 ToolCall -> 权限 -> 执行 -> ToolResult -> 回填消息 闭环。
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from src.engine import (
    MachineLoop,
    AgentResponse,
    BudgetPolicy,
    CancellationToken,
    GuardManager,
    HookManager,
    PermissionDecision,
    PermissionManager,
    ToolCall,
)
from src.profiles.coding.tools import CodingTools


class TestMachineLoop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "test.txt").write_text("hello world", encoding="utf-8")

        self.tools = CodingTools(self.root, max_output_chars=100)
        self.permission = PermissionManager()
        self.guard = GuardManager()
        self.budget = BudgetPolicy(max_turns=10)
        self.hooks = HookManager()
        self.cancel = CancellationToken()

    def tearDown(self):
        self.tmp.cleanup()

    def test_loop_with_one_tool_call(self):
        """测试：模型调一次工具，然后 done。"""
        # Mock 模型行为：
        # 第 1 轮返回 ToolCall
        # 第 2 轮返回 done
        call_count = [0]

        def mock_model_fn(messages):
            call_count[0] += 1
            if call_count[0] == 1:
                # 第 1 轮：调工具
                return AgentResponse(
                    content="我先读文件",
                    tool_calls=[
                        ToolCall(
                            id="call_1",
                            name="read_file",
                            arguments={"path": "test.txt"},
                        )
                    ],
                )
            # 第 2 轮：完成
            return AgentResponse(content="文件读完了", done=True)

        loop = MachineLoop(
            model_fn=mock_model_fn,
            tools=self.tools,
            permission=self.permission,
            guard=self.guard,
            budget=self.budget,
            final_verifier=lambda msgs, resp: resp.done,
            hooks=self.hooks,
        )

        result = loop.run([], self.cancel)
        self.assertEqual(result["status"], "success")
        self.assertIn("文件读完了", result["reply"])

    def test_loop_with_permission_deny(self):
        """测试：权限拒绝。"""
        # Mock 模型：第 1 轮调未知工具（会被拒绝），第 2 轮 done
        def mock_model_fn(messages):
            # 第 1 轮：被拒绝的工具
            if len(messages) == 0:
                return AgentResponse(
                    tool_calls=[
                        ToolCall(id="call_1", name="unknown", arguments={})
                    ]
                )
            # 第 2 轮：done
            return AgentResponse(content="算了", done=True)

        loop = MachineLoop(
            model_fn=mock_model_fn,
            tools=self.tools,
            permission=self.permission,
            guard=self.guard,
            budget=self.budget,
            final_verifier=lambda msgs, resp: resp.done,
            hooks=self.hooks,
        )

        result = loop.run([], self.cancel)
        self.assertEqual(result["status"], "success")

    def test_loop_cancel(self):
        """测试：取消。"""
        loop = MachineLoop(
            model_fn=lambda msgs: AgentResponse(content="unused"),
            tools=self.tools,
            permission=self.permission,
            guard=self.guard,
            budget=self.budget,
            final_verifier=lambda msgs, resp: resp.done,
            hooks=self.hooks,
        )

        self.cancel.cancel()
        result = loop.run([], self.cancel)
        self.assertEqual(result["status"], "cancelled")

    def test_loop_max_turns(self):
        """测试：超过预算。"""
        # Mock：永远返回工具调用，让它循环到 max_turns
        call_count = [0]

        def mock_model_fn(messages):
            call_count[0] += 1
            return AgentResponse(
                tool_calls=[
                    ToolCall(id=f"call_{call_count[0]}", name="read_file", arguments={"path": "test.txt"})
                ]
            )

        loop = MachineLoop(
            model_fn=mock_model_fn,
            tools=self.tools,
            permission=self.permission,
            guard=self.guard,
            budget=BudgetPolicy(max_turns=2),
            final_verifier=lambda msgs, resp: resp.done,
            hooks=self.hooks,
        )

        result = loop.run([], self.cancel)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "max_turns")


class TestGuardManager(unittest.TestCase):
    """GuardManager 的卡死检测测试。

    大白话：
      模型有时候会卡住——反复读同一个文件、反复搜同一个关键词。
      GuardManager 的职责就是发现这种卡死，然后叫停。

    这些测试只测 GuardManager 自己的判断逻辑，
    不涉及 MachineLoop，所以不需要 setUp/tearDown 创建临时目录。
    """

    def setUp(self):
        """每个测试开始前创建一个全新的 GuardManager。"""
        self.guard = GuardManager()

    # ------------------------------------------------------------------
    # 辅助方法：构造"模型在调工具"的对话历史
    # ------------------------------------------------------------------
    # messages 的结构是这样：
    #   [
    #       {"role": "system", "content": "..."},
    #       {"role": "user", "content": "..."},
    #       {"role": "assistant", "tool_calls": [{"id": "call_1", "function": {"name": "read_file", "arguments": "..."}}]},
    #       {"role": "tool", "tool_call_id": "call_1", "content": "..."},
    #       ...重复上面两组...
    #   ]
    #
    # 下面这个辅助函数能快速生成 N 轮"调同一个工具"的消息列表。
    # ------------------------------------------------------------------
    def _make_repeated_calls(self, name: str, arguments: dict, count: int):
        """生成 count 轮调同一个工具的消息列表。

        参数：
          name      — 工具名，比如 "read_file"
          arguments — 参数字典，比如 {"path": "test.txt"}
          count     — 重复多少次

        返回：
          list[dict] — 完整的对话历史，包含 assistant(tool_calls) + tool(result)
        """
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "帮我看看这个文件"},
        ]
        for i in range(count):
            # 第 i 轮：模型决定调工具
            messages.append({
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": str(arguments),
                        },
                    }
                ],
            })
            # 第 i 轮：工具返回结果
            messages.append({
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "content": f"这是第 {i} 次调 {name} 的结果",
            })
        return messages

    def _make_varied_calls(self, calls: list):
        """生成多轮调用不同工具的消息列表。

        参数：
          calls — list of (name, arguments) 元组，每轮调一个

        返回：
          list[dict] — 完整的对话历史
        """
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "帮我查几个东西"},
        ]
        for i, (name, arguments) in enumerate(calls):
            messages.append({
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": f"call_{i}",
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": str(arguments),
                        },
                    }
                ],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": f"call_{i}",
                "content": f"调 {name} 的结果",
            })
        return messages

    # ------------------------------------------------------------------
    # 测试用例
    # ------------------------------------------------------------------

    def test_repeat_same_tool_same_args(self):
        """连续 3 次同一工具 + 同一参数 → 应该拦截。

        这是最常见的卡死场景：
          模型连续 3 次调 read_file("test.txt")。
        """
        messages = self._make_repeated_calls("read_file", {"path": "test.txt"}, 3)
        self.assertTrue(self.guard.should_stop(messages, turn=3))

    def test_repeat_same_tool_same_args_4_times(self):
        """连续 4 次同一工具 + 同一参数 → 肯定拦截。

        如果 3 次就该停，4 次更没理由放过。
        """
        messages = self._make_repeated_calls("read_file", {"path": "test.txt"}, 4)
        self.assertTrue(self.guard.should_stop(messages, turn=4))

    def test_not_enough_calls(self):
        """只有 2 次工具调用 → 不拦截。

        很多时候工具确实需要调 2 次（比如先 grep 再 read_file）。
        不到 3 次不能武断说卡死。
        """
        messages = self._make_repeated_calls("read_file", {"path": "test.txt"}, 2)
        self.assertFalse(self.guard.should_stop(messages, turn=2))

    def test_different_tools(self):
        """连续调不同的工具 → 不拦截。

        模型在正常切换工具（先读文件、再搜代码），不是卡死。
        """
        messages = self._make_varied_calls([
            ("read_file", {"path": "a.txt"}),
            ("grep", {"query": "hello"}),
            ("list_dir", {"path": "src"}),
        ])
        self.assertFalse(self.guard.should_stop(messages, turn=3))

    def test_same_tool_different_args(self):
        """同一工具但不同参数 → 不拦截。

        模型在依次读不同文件，这是正常行为。
        只有"重复读同一个文件"才算卡死。
        """
        messages = self._make_varied_calls([
            ("read_file", {"path": "a.txt"}),
            ("read_file", {"path": "b.txt"}),
            ("read_file", {"path": "c.txt"}),
        ])
        self.assertFalse(self.guard.should_stop(messages, turn=3))

    def test_empty_messages(self):
        """空消息列表 → 不拦截。

        刚开始对话，什么都没干。
        """
        self.assertFalse(self.guard.should_stop([], turn=0))

    def test_only_system_and_user(self):
        """只有 system 和 user 消息，没有工具调用 → 不拦截。

        用户刚发消息，模型还没决定做什么。
        """
        messages = [
            {"role": "system", "content": "你是一个助手"},
            {"role": "user", "content": "帮我看看代码"},
        ]
        self.assertFalse(self.guard.should_stop(messages, turn=0))


class TestSimpleLLMAdapter(unittest.TestCase):
    """LLM 适配器的完成判定测试。

    大白话：
      完成判定按 tool-calling 协议的自然语义：
        带 tool_calls → 还想干活，不算完成
        不带 tool_calls、只输出文本 → 最终回复，算完成
        什么都没输出 → 异常，不算完成
      不再依赖 "## 总结" 文字标记。
    """

    def setUp(self):
        """创建一个干净的 adapter（不需要真正的 tools）。"""
        from src.profiles.coding.llm_adapter import StreamingAdapter
        from rich.console import Console

        # 测试用：给一个安静的 console 和简单的 theme
        test_console = Console(quiet=True)
        test_theme = {"dim": "grey50", "ai": "cyan"}

        self.adapter = StreamingAdapter(
            tools_schemas=[], console=test_console, theme=test_theme,
        )
        # 关闭流式：这些用例测的是 done 判断逻辑，不走网络
        self.adapter.stream = False

    # ------------------------------------------------------------------
    # 开始测试
    #  注意：这里不调真正的 LLM，直接构造 mock 的返回结果。
    #  我们只测试 adapter 的 done 判断逻辑，不测试 LLM 本身。
    # ------------------------------------------------------------------

    def test_done_when_no_tool_calls(self):
        """模型不调工具、只输出文本 → 应该标记为 done。

        模型完成工作后的自然表现：
          调完工具 → 不再调工具，直接汇报发现（不需要固定开头）。
        """
        # 假装 LLM 返回了汇报文本
        # 注意：没有 tool_calls，说明模型已经收工了
        raw_result = {
            "content": "找到了文件中的 ToolsCall 定义，在第 45 行。",
            "tool_calls": [],
        }

        # 模拟 llm_client.chat() 的返回格式
        # adapter 内部不直接调 LLM，所以我们要 mock 它的 chat 方法
        import src.profiles.coding.llm_adapter as adapter_module

        original_chat = adapter_module.chat
        try:
            adapter_module.chat = lambda messages, tools=None, **kw: raw_result

            response = self.adapter.call([])

            self.assertTrue(response.done)
            self.assertIn("ToolsCall", response.content)
        finally:
            adapter_module.chat = original_chat

    def test_not_done_with_tool_calls(self):
        """模型还带着 tool_calls → 不算 done。

        哪怕它同时说了话（比如“我先读一下文件”），
        只要有工具调用就说明它还想继续干活。
        """
        raw_result = {
            "content": "我先读一下这个文件的内容。",
            "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "read_file", "arguments": '{"path": "test.txt"}'}}
            ],
        }

        import src.profiles.coding.llm_adapter as adapter_module

        original_chat = adapter_module.chat
        try:
            adapter_module.chat = lambda messages, tools=None, **kw: raw_result

            response = self.adapter.call([])

            self.assertFalse(response.done)
        finally:
            adapter_module.chat = original_chat

    def test_done_plain_text_reply(self):
        """普通文本回复（无工具调用）→ 算 done。

        闲聊、直接回答问题都属于这种：
        模型不需要硬凑 "## 总结" 开头也能自然收尾。
        """
        raw_result = {
            "content": "这个文件内容不多，没有找到你说的那个标记。",
            "tool_calls": [],
        }

        import src.profiles.coding.llm_adapter as adapter_module

        original_chat = adapter_module.chat
        try:
            adapter_module.chat = lambda messages, tools=None, **kw: raw_result

            response = self.adapter.call([])

            self.assertTrue(response.done)
        finally:
            adapter_module.chat = original_chat

    def test_not_done_with_empty_content(self):
        """模型什么都没说、也没调工具 → 不算 done。

        极端情况：LLM 返回了空字符串。
        这通常意味着请求出问题了，不能算完成，
        交给 Loop 按 no_tool_call 异常处理。
        """
        raw_result = {
            "content": "",
            "tool_calls": [],
        }

        import src.profiles.coding.llm_adapter as adapter_module

        original_chat = adapter_module.chat
        try:
            adapter_module.chat = lambda messages, tools=None, **kw: raw_result

            response = self.adapter.call([])

            self.assertFalse(response.done)
        finally:
            adapter_module.chat = original_chat


    def test_streaming_done_and_metrics(self):
        """流式路径：done 判断 + TTFT/token 指标累积 + last_streamed 标记。

        模拟 chat_stream 边回调 token 边返回 usage/ttft，验证适配器正确解析。
        """
        import src.profiles.coding.llm_adapter as adapter_module

        def fake_stream(messages, tools=None, on_token=None, **kw):
            # 模拟流式输出两个 content 增量
            if on_token:
                on_token("任务完成，")
                on_token("测试全部通过。")
            return {
                "content": "任务完成，测试全部通过。",
                "tool_calls": [],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "ttft_ms": 123.0,
            }

        original = adapter_module.chat_stream
        try:
            adapter_module.chat_stream = fake_stream
            self.adapter.stream = True
            self.adapter.reset_metrics()

            response = self.adapter.call([])

            self.assertTrue(response.done)
            self.assertIn("任务完成", response.content)
            self.assertTrue(self.adapter.last_streamed)
            self.assertEqual(self.adapter.total_prompt_tokens, 100)
            self.assertEqual(self.adapter.total_completion_tokens, 20)
            self.assertAlmostEqual(self.adapter.last_ttft_ms, 123.0)
        finally:
            adapter_module.chat_stream = original
            self.adapter.stream = False


if __name__ == "__main__":
    unittest.main()
