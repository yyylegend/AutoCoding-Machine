"""Plan Mode 测试：只读约束 + 权限拦截 + 系统提示注入。

覆盖三块：
1. PermissionManager plan_mode - 写工具被拒、只读工具放行
2. plan_mode.py - 计划指令内容
3. system_prompt - 默认不注入 Plan Mode 规则（Plan 指令走 injections）
"""

import unittest

from src.engine import PermissionDecision, PermissionManager, ToolCall
from src.profiles.coding.plan_mode import PLAN_MODE_INSTRUCTIONS, get_plan_mode_injection
from src.profiles.coding.system_prompt import get_system_prompt


class TestPermissionManagerPlanMode(unittest.TestCase):
    """Plan Mode 下写工具被拒绝，只读工具放行。"""

    def setUp(self):
        self.perm = PermissionManager()  # 无 tool_manager，走 tool_defaults 表

    def test_write_tools_denied_in_plan_mode(self):
        """Plan Mode 下 write_file / edit_file / run_bash 等全部 DENY。"""
        self.perm.plan_mode = True
        for name in ["write_file", "edit_file", "run_bash", "run_test"]:
            decision = self.perm.check(ToolCall(id="c", name=name, arguments={}))
            self.assertEqual(decision, PermissionDecision.DENY, f"{name} 应被拒绝")

    def test_read_tools_allowed_in_plan_mode(self):
        """Plan Mode 下只读工具仍 AUTO。"""
        self.perm.plan_mode = True
        for name in ["read_file", "list_dir", "glob", "grep"]:
            decision = self.perm.check(ToolCall(id="c", name=name, arguments={}))
            self.assertEqual(decision, PermissionDecision.AUTO, f"{name} 应放行")

    def test_normal_mode_unchanged(self):
        """非 Plan Mode：写工具仍 ASK，行为不变。"""
        self.perm.plan_mode = False
        self.assertEqual(
            self.perm.check(ToolCall(id="c", name="write_file", arguments={})),
            PermissionDecision.ASK,
        )
        self.assertEqual(
            self.perm.check(ToolCall(id="c", name="read_file", arguments={})),
            PermissionDecision.AUTO,
        )

    def test_constructor_flag(self):
        """构造时传 plan_mode=True 也生效。"""
        perm = PermissionManager(plan_mode=True)
        self.assertEqual(
            perm.check(ToolCall(id="c", name="run_bash", arguments={})),
            PermissionDecision.DENY,
        )

    def test_auto_approve_does_not_bypass_plan_mode(self):
        """auto_approve（评测模式）不能绕过 Plan Mode 的写工具拦截。"""
        perm = PermissionManager(auto_approve=True, plan_mode=True)
        self.assertEqual(
            perm.check(ToolCall(id="c", name="write_file", arguments={})),
            PermissionDecision.DENY,
        )


class TestPlanModeInjection(unittest.TestCase):
    """计划指令注入测试。"""

    def test_get_plan_mode_injection_returns_instructions(self):
        """get_plan_mode_injection 返回计划指令，包含关键要求。"""
        self.assertEqual(get_plan_mode_injection(), PLAN_MODE_INSTRUCTIONS)
        self.assertIn("Plan Mode", PLAN_MODE_INSTRUCTIONS)
        self.assertIn("只读", PLAN_MODE_INSTRUCTIONS)
        self.assertIn("结构化计划", PLAN_MODE_INSTRUCTIONS)

    def test_system_prompt_without_plan_mode(self):
        """默认 system prompt 不包含 Plan Mode 规则（Plan 指令走 injections）。"""
        prompt = get_system_prompt("test_workspace")
        self.assertNotIn("Plan Mode", prompt)


if __name__ == "__main__":
    unittest.main()
