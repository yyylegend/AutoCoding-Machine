"""engine.contracts 基础测试。

只验证数据结构是否正确，不测 Agent 行为。
"""

import unittest

from src.engine.contracts import (
    AgentResponse,
    BudgetPolicy,
    CancellationToken,
    PermissionDecision,
    ToolCall,
    ToolResult,
)


class TestEngineContracts(unittest.TestCase):
    def test_tool_call_fields(self):
        call = ToolCall(
            id="call_1",
            name="read_file",
            arguments={"path": "src/main.py"},
        )
        self.assertEqual(call.id, "call_1")
        self.assertEqual(call.name, "read_file")
        self.assertEqual(call.arguments["path"], "src/main.py")

    def test_agent_response_to_message_with_tool_calls(self):
        response = AgentResponse(
            content="准备读文件",
            tool_calls=[
                ToolCall(
                    id="call_1",
                    name="read_file",
                    arguments={"path": "src/main.py"},
                )
            ],
        )
        message = response.to_message()
        self.assertEqual(message["role"], "assistant")
        self.assertEqual(message["content"], "准备读文件")
        self.assertEqual(len(message["tool_calls"]), 1)
        self.assertEqual(message["tool_calls"][0]["id"], "call_1")
        self.assertEqual(message["tool_calls"][0]["function"]["name"], "read_file")

    def test_tool_result_to_message(self):
        result = ToolResult(
            tool_call_id="call_1",
            content="hello",
            error=False,
        )
        message = result.to_message()
        self.assertEqual(message["role"], "tool")
        self.assertEqual(message["tool_call_id"], "call_1")
        self.assertEqual(message["content"], "hello")

    def test_tool_result_error_fields(self):
        result = ToolResult(
            tool_call_id="call_1",
            content="path outside workspace",
            error=True,
            error_type="permission",
            retryable=False,
        )
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")
        self.assertFalse(result.retryable)

    def test_permission_decision_values(self):
        self.assertEqual(PermissionDecision.AUTO.value, "auto")
        self.assertEqual(PermissionDecision.ASK.value, "ask")
        self.assertEqual(PermissionDecision.DENY.value, "deny")

    def test_budget_policy_defaults(self):
        budget = BudgetPolicy()
        self.assertEqual(budget.max_turns, 50)
        self.assertEqual(budget.timeout_per_tool, 120)

    def test_cancellation_token(self):
        token = CancellationToken()
        self.assertFalse(token.is_cancelled())
        token.cancel()
        self.assertTrue(token.is_cancelled())


if __name__ == "__main__":
    unittest.main()
