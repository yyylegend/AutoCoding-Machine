"""run_bash 工具测试。

测试内容：
  - 白名单检查：_check_whitelist 函数（pytest/python/npm/pip/git 通过，其他拒绝）
  - 元字符检查：_is_safe_command 函数（| ; && || > < $ ` ( ) 被拒绝）
  - 白名单内命令正常执行（python --version, pytest）
  - 白名单外命令被拒绝（rm, curl, wget, sudo）
  - 含 shell 元字符的命令被拒绝（管道、命令替换、反引号）
  - 超时处理（Mock subprocess）
  - run_test 别名仍可用
  - 权限：run_bash 返回 ASK
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.engine.contracts import PermissionDecision, ToolCall
from src.engine.permission_manager import PermissionManager
from src.profiles.coding.tools import CodingTools
from src.profiles.coding.tools.run_bash import (
    ALLOWED_COMMANDS,
    _check_whitelist,
    _is_safe_command,
)


class TestWhitelistCheck(unittest.TestCase):
    """白名单检查函数 _check_whitelist 的单元测试。

    直接测试纯函数，不涉及 subprocess，速度快且稳定。
    """

    def test_pytest_in_whitelist(self):
        """pytest 在白名单内。"""
        self.assertTrue(_check_whitelist("pytest tests/"))

    def test_python_in_whitelist(self):
        """python 在白名单内。"""
        self.assertTrue(_check_whitelist("python script.py"))

    def test_python_full_path_in_whitelist(self):
        """完整路径的 python 也能匹配（取 basename）。"""
        # 模型可能传 sys.executable 完整路径
        self.assertTrue(_check_whitelist("e:\\venv\\Scripts\\python.exe script.py"))

    def test_python_exe_no_path_in_whitelist(self):
        """python.exe（带后缀）也能匹配。"""
        self.assertTrue(_check_whitelist("python.exe script.py"))

    def test_npm_in_whitelist(self):
        """npm 在白名单内。"""
        self.assertTrue(_check_whitelist("npm install"))

    def test_pip_in_whitelist(self):
        """pip 在白名单内。"""
        self.assertTrue(_check_whitelist("pip install requests"))

    def test_git_in_whitelist(self):
        """git 在白名单内。"""
        self.assertTrue(_check_whitelist("git status"))

    def test_rm_not_in_whitelist(self):
        """rm 不在白名单内。"""
        self.assertFalse(_check_whitelist("rm -rf /"))

    def test_curl_not_in_whitelist(self):
        """curl 不在白名单内。"""
        self.assertFalse(_check_whitelist("curl http://example.com"))

    def test_wget_not_in_whitelist(self):
        """wget 不在白名单内。"""
        self.assertFalse(_check_whitelist("wget http://example.com"))

    def test_sudo_not_in_whitelist(self):
        """sudo 不在白名单内。"""
        self.assertFalse(_check_whitelist("sudo rm -rf /"))

    def test_empty_command_not_in_whitelist(self):
        """空命令不在白名单内。"""
        self.assertFalse(_check_whitelist(""))
        self.assertFalse(_check_whitelist("   "))


class TestForbiddenChars(unittest.TestCase):
    """Shell 元字符检查函数 _is_safe_command 的单元测试。

    验证所有 FORBIDDEN_CHARS 都能被正确拦截。
    """

    def test_pipe_rejected(self):
        """管道符 | 被拒绝。"""
        self.assertFalse(_is_safe_command("ls | rm -rf /"))

    def test_semicolon_rejected(self):
        """分号 ; 被拒绝。"""
        self.assertFalse(_is_safe_command("ls; rm -rf /"))

    def test_and_rejected(self):
        """&& 被拒绝。"""
        self.assertFalse(_is_safe_command("ls && rm -rf /"))

    def test_or_rejected(self):
        """|| 被拒绝。"""
        self.assertFalse(_is_safe_command("ls || rm"))

    def test_redirect_out_rejected(self):
        """重定向 > 被拒绝。"""
        self.assertFalse(_is_safe_command("ls > /etc/passwd"))

    def test_redirect_in_rejected(self):
        """重定向 < 被拒绝。"""
        self.assertFalse(_is_safe_command("cat < /etc/passwd"))

    def test_dollar_rejected(self):
        """$ 变量展开被拒绝。"""
        self.assertFalse(_is_safe_command("echo $(whoami)"))

    def test_backtick_rejected(self):
        """反引号命令替换被拒绝。"""
        self.assertFalse(_is_safe_command("echo `whoami`"))

    def test_parens_rejected(self):
        """括号 ( ) 被拒绝。"""
        self.assertFalse(_is_safe_command("echo (test)"))

    def test_safe_commands_pass(self):
        """安全命令通过检查。"""
        self.assertTrue(_is_safe_command("pytest tests/"))
        self.assertTrue(_is_safe_command("python script.py"))
        self.assertTrue(_is_safe_command("git status"))
        self.assertTrue(_is_safe_command("npm install"))


class TestRunBashExecute(unittest.TestCase):
    """run_bash 工具实际执行测试。

    通过 CodingTools 端到端调用，验证完整流程。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tools = CodingTools(self.root, max_output_chars=5000)

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, **kwargs):
        """构造一个 run_bash 的 ToolCall。"""
        return ToolCall(id="call_1", name="run_bash", arguments=kwargs)

    def test_python_version_executes(self):
        """python --version 正常执行。"""
        tc = self._call(command=f"{sys.executable} --version")
        result = self.tools.execute(tc)
        self.assertFalse(result.error)
        self.assertEqual(result.metadata["exit_code"], 0)

    def test_pytest_executes(self):
        """pytest 正常执行并通过。"""
        # 创建一个会通过的测试文件
        (self.root / "test_ok.py").write_text(
            "def test_ok():\n    assert 1 + 1 == 2\n",
            encoding="utf-8",
        )
        tc = self._call(command=f"{sys.executable} -m pytest test_ok.py -q")
        result = self.tools.execute(tc)
        self.assertFalse(result.error)
        self.assertEqual(result.metadata["exit_code"], 0)

    def test_non_zero_exit_code_not_error(self):
        """非零退出码不算 error，只是标记返回码。"""
        # 创建一个会失败的测试文件
        (self.root / "test_fail.py").write_text(
            "def test_fail():\n    assert False\n",
            encoding="utf-8",
        )
        tc = self._call(command=f"{sys.executable} -m pytest test_fail.py -q")
        result = self.tools.execute(tc)
        # 工具执行成功，但 exit_code != 0
        self.assertFalse(result.error)
        self.assertNotEqual(result.metadata["exit_code"], 0)

    def test_rm_rejected(self):
        """rm 命令被白名单拒绝。"""
        tc = self._call(command="rm -rf /")
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_curl_rejected(self):
        """curl 命令被白名单拒绝。"""
        tc = self._call(command="curl http://example.com")
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_wget_rejected(self):
        """wget 命令被白名单拒绝。"""
        tc = self._call(command="wget http://example.com")
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_sudo_rejected(self):
        """sudo 命令被白名单拒绝。"""
        tc = self._call(command="sudo rm -rf /")
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_pipe_rejected(self):
        """管道命令被元字符检查拒绝。"""
        tc = self._call(command="ls | rm -rf /")
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_command_substitution_rejected(self):
        """命令替换 $(...) 被元字符检查拒绝。"""
        tc = self._call(command="echo $(whoami)")
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_backtick_rejected(self):
        """反引号命令替换被拒绝。"""
        tc = self._call(command="echo `whoami`")
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_redirect_rejected(self):
        """重定向被元字符检查拒绝。"""
        tc = self._call(command="git log > output.txt")
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_missing_command(self):
        """缺 command 参数返回 invalid_args。"""
        tc = self._call()
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "invalid_args")

    def test_empty_command(self):
        """空 command 参数返回 invalid_args。"""
        tc = self._call(command="   ")
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "invalid_args")

    def test_output_truncation(self):
        """输出超长时被截断。"""
        # 创建一个输出很多的测试
        (self.root / "test_verbose.py").write_text(
            "def test_verbose():\n"
            "    for i in range(1000):\n"
            "        print(f'line {i} ' + 'x' * 100)\n",
            encoding="utf-8",
        )
        # 用很小的 max_output_chars 触发截断
        small_tools = CodingTools(self.root, max_output_chars=200)
        tc = ToolCall(id="call_1", name="run_bash", arguments={
            "command": f"{sys.executable} -m pytest test_verbose.py -q -s",
        })
        result = small_tools.execute(tc)
        self.assertFalse(result.error)
        self.assertTrue(result.metadata["truncated"])

    def test_metadata_contains_command(self):
        """metadata 里包含执行的命令。"""
        tc = self._call(command=f"{sys.executable} --version")
        result = self.tools.execute(tc)
        self.assertFalse(result.error)
        self.assertIn("command", result.metadata)


class TestRunBashTimeout(unittest.TestCase):
    """超时处理测试。

    用 Mock 避免真的等 180 秒。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tools = CodingTools(self.root, max_output_chars=5000)

    def tearDown(self):
        self.tmp.cleanup()

    @patch("src.profiles.coding.tools.run_bash.subprocess.run")
    def test_timeout_handled(self, mock_run):
        """超时被正确捕获并返回 execution 错误。"""
        import subprocess as sp
        mock_run.side_effect = sp.TimeoutExpired(cmd="pytest", timeout=180)
        tc = ToolCall(id="t1", name="run_bash", arguments={
            "command": "pytest tests/",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "execution")
        self.assertIn("超时", result.content)

    @patch("src.profiles.coding.tools.run_bash.subprocess.run")
    def test_command_not_found_handled(self, mock_run):
        """命令不存在被正确捕获。"""
        mock_run.side_effect = FileNotFoundError("pip not found")
        tc = ToolCall(id="t2", name="run_bash", arguments={
            "command": "pip --version",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "execution")
        self.assertIn("找不到命令", result.content)


class TestRunTestAlias(unittest.TestCase):
    """run_test 别名仍可用测试。

    run_test 现在是 run_bash 的别名，行为应与 run_bash 一致。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tools = CodingTools(self.root, max_output_chars=5000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_test_still_works(self):
        """run_test 别名仍能执行 pytest。"""
        (self.root / "test_ok.py").write_text(
            "def test_ok():\n    assert True\n",
            encoding="utf-8",
        )
        tc = ToolCall(id="a1", name="run_test", arguments={
            "command": f"{sys.executable} -m pytest test_ok.py -q",
        })
        result = self.tools.execute(tc)
        self.assertFalse(result.error)
        self.assertEqual(result.metadata["exit_code"], 0)

    def test_run_test_rejects_non_whitelist(self):
        """run_test 别名也拒绝白名单外命令。"""
        tc = ToolCall(id="a2", name="run_test", arguments={
            "command": "rm -rf /",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_run_test_rejects_metachars(self):
        """run_test 别名也拒绝含元字符的命令。"""
        tc = ToolCall(id="a3", name="run_test", arguments={
            "command": "ls | rm -rf /",
        })
        result = self.tools.execute(tc)
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")


class TestRunBashPermission(unittest.TestCase):
    """权限测试：run_bash 返回 ASK。"""

    def test_run_bash_is_ask_fallback(self):
        """无 ToolManager 时回落到硬编码表也是 ASK。"""
        perm = PermissionManager()
        tc = ToolCall(id="p1", name="run_bash", arguments={"command": "pytest"})
        self.assertEqual(perm.check(tc), PermissionDecision.ASK)

    def test_run_bash_is_ask_with_tool_manager(self):
        """通过 ToolManager 注册时 run_bash 权限为 ASK。"""
        from src.engine.tool_manager import ToolManager
        from src.profiles.coding.sandbox import WorkspaceSandbox
        from src.profiles.coding.tools import run_bash

        tmp = tempfile.TemporaryDirectory()
        try:
            tm = ToolManager(WorkspaceSandbox(tmp.name))
            tm.register(run_bash)
            perm = PermissionManager(tool_manager=tm)
            tc = ToolCall(id="p2", name="run_bash", arguments={"command": "pytest"})
            self.assertEqual(perm.check(tc), PermissionDecision.ASK)
        finally:
            tmp.cleanup()

    def test_allowed_commands_list(self):
        """白名单包含 5 个命令。"""
        self.assertEqual(
            set(ALLOWED_COMMANDS),
            {"pytest", "python", "npm", "pip", "git"},
        )


if __name__ == "__main__":
    unittest.main()
