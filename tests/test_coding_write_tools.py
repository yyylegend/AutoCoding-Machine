"""Phase 4 写工具测试：write_file / edit_file / run_test。

测试内容：
  - write_file：正常写入 / 越界拒绝 / 自动建目录 / 缺参数
  - edit_file：正常替换 / old_text 不存在 / old_text 出现多次 / 越界
  - run_test：正常跑 pytest / 非白名单命令拒绝 / 输出截断
  - 权限：write_file/edit_file/run_test 返回 ASK
"""

import sys
import tempfile
import unittest
from pathlib import Path

from src.engine.contracts import PermissionDecision, ToolCall
from src.engine.permission_manager import PermissionManager
from src.profiles.coding.tools import CodingTools


class TestWriteFile(unittest.TestCase):
    """write_file 工具测试。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tools = CodingTools(self.root, max_output_chars=5000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_new_file(self):
        """正常写入新文件。"""
        tc = ToolCall(id="w1", name="write_file", arguments={
            "path": "hello.txt",
            "content": "hello world",
        })
        result = self.tools.execute(tc)
        self.assertFalse(result.error)
        self.assertIn("hello.txt", result.content)
        # 验证文件真的写进去了
        self.assertEqual((self.root / "hello.txt").read_text(encoding="utf-8"), "hello world")

    def test_write_creates_parent_dirs(self):
        """自动创建父目录。"""
        tc = ToolCall(id="w2", name="write_file", arguments={
            "path": "a/b/c/deep.txt",
            "content": "deep content",
        })
        result = self.tools.execute(tc)
        self.assertFalse(result.error)
        self.assertTrue((self.root / "a" / "b" / "c" / "deep.txt").exists())

    def test_write_overwrite_existing(self):
        """覆盖已有文件。"""
        (self.root / "exist.txt").write_text("old", encoding="utf-8")
        tc = ToolCall(id="w3", name="write_file", arguments={
            "path": "exist.txt",
            "content": "new content",
        })
        result = self.tools.execute(tc)
        self.assertFalse(result.error)
        self.assertEqual((self.root / "exist.txt").read_text(encoding="utf-8"), "new content")

    def test_write_path_escape(self):
        """越界写入被拒绝。"""
        tc = ToolCall(id="w4", name="write_file", arguments={
            "path": "../../etc/evil.txt",
            "content": "bad",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_write_missing_path(self):
        """缺 path 参数。"""
        tc = ToolCall(id="w5", name="write_file", arguments={
            "content": "no path",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "invalid_args")

    def test_write_missing_content(self):
        """缺 content 参数。"""
        tc = ToolCall(id="w6", name="write_file", arguments={
            "path": "test.txt",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "invalid_args")

    def test_write_empty_content(self):
        """空 content 允许（创建空文件）。"""
        tc = ToolCall(id="w7", name="write_file", arguments={
            "path": "empty.txt",
            "content": "",
        })
        result = self.tools.execute(tc)
        self.assertFalse(result.error)
        self.assertEqual((self.root / "empty.txt").read_text(encoding="utf-8"), "")


class TestEditFile(unittest.TestCase):
    """edit_file 工具测试。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tools = CodingTools(self.root, max_output_chars=5000)
        # 创建一个测试文件
        (self.root / "main.py").write_text(
            "def hello():\n    print('hello')\n\ndef world():\n    print('world')\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_edit_normal_replace(self):
        """正常替换。"""
        tc = ToolCall(id="e1", name="edit_file", arguments={
            "path": "main.py",
            "old_text": "print('hello')",
            "new_text": "print('HELLO!')",
        })
        result = self.tools.execute(tc)
        self.assertFalse(result.error)
        content = (self.root / "main.py").read_text(encoding="utf-8")
        self.assertIn("HELLO!", content)
        self.assertNotIn("print('hello')", content)

    def test_edit_old_text_not_found(self):
        """old_text 不存在。"""
        tc = ToolCall(id="e2", name="edit_file", arguments={
            "path": "main.py",
            "old_text": "this does not exist",
            "new_text": "replacement",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "execution")
        self.assertIn("找不到", result.content)

    def test_edit_old_text_multiple(self):
        """old_text 出现多次。"""
        tc = ToolCall(id="e3", name="edit_file", arguments={
            "path": "main.py",
            "old_text": "print(",
            "new_text": "log(",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "execution")
        self.assertIn("不唯一", result.content)

    def test_edit_path_escape(self):
        """越界编辑被拒绝。"""
        tc = ToolCall(id="e4", name="edit_file", arguments={
            "path": "../../etc/passwd",
            "old_text": "root",
            "new_text": "hacked",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_edit_file_not_exist(self):
        """编辑不存在的文件。"""
        tc = ToolCall(id="e5", name="edit_file", arguments={
            "path": "nonexist.py",
            "old_text": "x",
            "new_text": "y",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "execution")

    def test_edit_missing_old_text(self):
        """缺 old_text 参数。"""
        tc = ToolCall(id="e6", name="edit_file", arguments={
            "path": "main.py",
            "new_text": "y",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "invalid_args")


class TestRunTest(unittest.TestCase):
    """run_test 工具测试。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tools = CodingTools(self.root, max_output_chars=5000)
        # 创建一个简单测试文件
        (self.root / "test_sample.py").write_text(
            "def test_pass():\n    assert 1 + 1 == 2\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_pytest_pass(self):
        """正常跑 pytest 通过。"""
        tc = ToolCall(id="r1", name="run_test", arguments={
            "command": f"{sys.executable} -m pytest test_sample.py -q",
        })
        result = self.tools.execute(tc)
        self.assertFalse(result.error)
        # run_test 现在是 run_bash 别名，成功时返回“命令执行成功”
        self.assertIn("成功", result.content)
        self.assertEqual(result.metadata["exit_code"], 0)

    def test_run_pytest_fail(self):
        """pytest 失败（exit_code != 0）但工具本身不算 error。"""
        (self.root / "test_fail.py").write_text(
            "def test_fail():\n    assert False\n",
            encoding="utf-8",
        )
        tc = ToolCall(id="r2", name="run_test", arguments={
            "command": f"{sys.executable} -m pytest test_fail.py -q",
        })
        result = self.tools.execute(tc)
        # 工具执行成功（没有 error），但 exit_code != 0
        self.assertFalse(result.error)
        self.assertNotEqual(result.metadata["exit_code"], 0)

    def test_run_non_whitelist_command(self):
        """非白名单命令被拒绝。"""
        tc = ToolCall(id="r3", name="run_test", arguments={
            "command": "rm -rf /",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_run_curl_rejected(self):
        """白名单外命令（curl）被拒绝。

        注意：run_test 现在是 run_bash 的别名，
        白名单包含 pytest/python/npm/pip/git，
        所以 pip 不再被拒绝，改用 curl 验证白名单外拒绝。
        """
        tc = ToolCall(id="r4", name="run_test", arguments={
            "command": "curl http://example.com",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_run_missing_command(self):
        """缺 command 参数。"""
        tc = ToolCall(id="r5", name="run_test", arguments={})
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "invalid_args")

    def test_run_output_truncation(self):
        """输出截断。"""
        # 创建一个输出很多的测试
        (self.root / "test_verbose.py").write_text(
            "def test_verbose():\n"
            "    for i in range(1000):\n"
            "        print(f'line {i} ' + 'x' * 100)\n",
            encoding="utf-8",
        )
        # 用很小的 max_output_chars
        small_tools = CodingTools(self.root, max_output_chars=200)
        tc = ToolCall(id="r6", name="run_test", arguments={
            "command": f"{sys.executable} -m pytest test_verbose.py -q -s",
        })
        result = small_tools.execute(tc)
        self.assertFalse(result.error)
        self.assertTrue(result.metadata["truncated"])


class TestWritePermissions(unittest.TestCase):
    """权限测试：写操作工具返回 ASK。"""

    def setUp(self):
        self.perm = PermissionManager()

    def test_write_file_is_ask(self):
        tc = ToolCall(id="p1", name="write_file", arguments={"path": "x.py", "content": ""})
        self.assertEqual(self.perm.check(tc), PermissionDecision.ASK)

    def test_edit_file_is_ask(self):
        tc = ToolCall(id="p2", name="edit_file", arguments={"path": "x.py", "old_text": "a", "new_text": "b"})
        self.assertEqual(self.perm.check(tc), PermissionDecision.ASK)

    def test_run_test_is_ask(self):
        tc = ToolCall(id="p3", name="run_test", arguments={"command": "pytest"})
        self.assertEqual(self.perm.check(tc), PermissionDecision.ASK)

    def test_read_file_still_auto(self):
        tc = ToolCall(id="p4", name="read_file", arguments={"path": "x.py"})
        self.assertEqual(self.perm.check(tc), PermissionDecision.AUTO)

    def test_unknown_tool_deny(self):
        tc = ToolCall(id="p5", name="delete_everything", arguments={})
        self.assertEqual(self.perm.check(tc), PermissionDecision.DENY)


if __name__ == "__main__":
    unittest.main()
