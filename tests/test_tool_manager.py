"""ToolManager 与 @tool 装饰器测试。

覆盖：
  1. @tool 装饰器正确挂 _tool_meta 元数据
  2. register + execute 分发正确
  3. get_schemas 返回所有 schema
  4. 未知工具返回 invalid_args 错误
  5. get_permission 三种情况（有装饰器 / 无装饰器 / 未注册）
  6. PermissionManager 带 tool_manager 读装饰器权限、不带时行为不变
"""

import tempfile
import types
import unittest
from pathlib import Path

from src.engine.contracts import PermissionDecision, ToolCall
from src.engine.permission_manager import PermissionManager
from src.engine.tool_manager import ToolManager, tool
from src.profiles.coding.sandbox import WorkspaceSandbox
from src.profiles.coding.tools import read_file, write_file


def _make_legacy_module():
    """造一个没有 @tool 装饰器的假工具模块（模拟老工具）。

    返回：
      一个 types.SimpleNamespace，带 execute 和 schema 两个函数，
      名字只能从 schema()["function"]["name"] 读到。
    """
    def execute(tool_call, sandbox, max_output_chars):
        # 假工具：什么都不做，回显一句话
        from src.engine.contracts import ToolResult
        return ToolResult(tool_call_id=tool_call.id, content="legacy ok")

    def schema():
        return {
            "type": "function",
            "function": {
                "name": "legacy_tool",
                "description": "老工具，没有装饰器",
                "parameters": {"type": "object", "properties": {}},
            },
        }

    return types.SimpleNamespace(execute=execute, schema=schema)


class TestToolDecorator(unittest.TestCase):
    """@tool 装饰器本身的行为。"""

    def test_meta_attached(self):
        # 装饰后函数应带 _tool_meta，且值和声明一致
        @tool(name="demo", permission="ask", timeout=30)
        def execute(tool_call, sandbox, max_output_chars):
            return "ran"

        self.assertEqual(execute._tool_meta["name"], "demo")
        self.assertEqual(execute._tool_meta["permission"], "ask")
        self.assertEqual(execute._tool_meta["timeout"], 30)

    def test_defaults(self):
        # 只给 name 时，permission/timeout 用默认值
        @tool(name="demo2")
        def execute(tool_call, sandbox, max_output_chars):
            return "ran"

        self.assertEqual(execute._tool_meta["permission"], "auto")
        self.assertEqual(execute._tool_meta["timeout"], 120)

    def test_function_behavior_unchanged(self):
        # 装饰器不能改变函数的调用行为
        @tool(name="demo3")
        def execute(tool_call, sandbox, max_output_chars):
            return "ran"

        self.assertEqual(execute(None, None, 100), "ran")

    def test_real_tools_have_meta(self):
        # 项目里真实工具已加装饰器，名字要和 schema 一致
        self.assertEqual(
            read_file.execute._tool_meta["name"],
            read_file.schema()["function"]["name"],
        )
        self.assertEqual(write_file.execute._tool_meta["permission"], "ask")


class TestToolManager(unittest.TestCase):
    """ToolManager 注册 / 分发 / 查询。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "hello.txt").write_text("hello world\n", encoding="utf-8")
        self.manager = ToolManager(WorkspaceSandbox(self.root), max_output_chars=100)
        self.manager.register(read_file)

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, name, **kwargs):
        return ToolCall(id="call_1", name=name, arguments=kwargs)

    def test_execute_dispatch(self):
        # 注册后按名字分发，能真正读到文件内容
        result = self.manager.execute(self._call("read_file", path="hello.txt"))
        self.assertFalse(result.error)
        self.assertIn("hello world", result.content)

    def test_unknown_tool(self):
        # 未注册的名字 -> invalid_args 错误回执
        result = self.manager.execute(self._call("no_such_tool"))
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "invalid_args")
        self.assertEqual(result.tool_call_id, "call_1")

    def test_register_legacy_module(self):
        # 无装饰器模块：名字从 schema 里读（向后兼容）
        legacy = _make_legacy_module()
        self.manager.register(legacy)
        self.assertTrue(self.manager.is_registered("legacy_tool"))
        result = self.manager.execute(self._call("legacy_tool"))
        self.assertFalse(result.error)
        self.assertEqual(result.content, "legacy ok")

    def test_get_schemas(self):
        # 每注册一个工具，schema 列表就多一份，且名字对得上
        self.manager.register(write_file)
        schemas = self.manager.get_schemas()
        names = []
        for item in schemas:
            names.append(item["function"]["name"])
        self.assertEqual(names, ["read_file", "write_file"])

    def test_get_permission_with_decorator(self):
        # 有装饰器：返回声明的权限
        self.manager.register(write_file)
        self.assertEqual(self.manager.get_permission("read_file"), "auto")
        self.assertEqual(self.manager.get_permission("write_file"), "ask")

    def test_get_permission_without_decorator(self):
        # 注册了但没装饰器：兼容期默认 "auto"
        self.manager.register(_make_legacy_module())
        self.assertEqual(self.manager.get_permission("legacy_tool"), "auto")

    def test_get_permission_unregistered(self):
        # 未注册：一律 "deny"
        self.assertEqual(self.manager.get_permission("no_such_tool"), "deny")


class TestPermissionManagerWithToolManager(unittest.TestCase):
    """PermissionManager 与 ToolManager 的配合。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = ToolManager(WorkspaceSandbox(self.tmp.name))
        self.manager.register(read_file)
        self.manager.register(write_file)

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, name):
        return ToolCall(id="call_1", name=name, arguments={})

    def test_reads_decorator_permission(self):
        # 带 tool_manager：权限来自工具的 @tool 装饰器
        perm = PermissionManager(tool_manager=self.manager)
        self.assertEqual(perm.check(self._call("read_file")), PermissionDecision.AUTO)
        self.assertEqual(perm.check(self._call("write_file")), PermissionDecision.ASK)

    def test_unregistered_falls_back_to_defaults(self):
        # grep 没注册进 manager，但硬编码表里有 -> 回落到表，仍是 AUTO
        perm = PermissionManager(tool_manager=self.manager)
        self.assertEqual(perm.check(self._call("grep")), PermissionDecision.AUTO)
        # 哪儿都没有的工具 -> DENY
        self.assertEqual(perm.check(self._call("rm_rf")), PermissionDecision.DENY)

    def test_without_tool_manager_unchanged(self):
        # 不传 tool_manager：行为和以前完全一样
        perm = PermissionManager()
        self.assertEqual(perm.check(self._call("read_file")), PermissionDecision.AUTO)
        self.assertEqual(perm.check(self._call("write_file")), PermissionDecision.ASK)
        self.assertEqual(perm.check(self._call("unknown")), PermissionDecision.DENY)


if __name__ == "__main__":
    unittest.main()
