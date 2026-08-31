"""Coding 只读工具测试。"""

import tempfile
import unittest
from pathlib import Path

from src.engine.contracts import ToolCall
from src.profiles.coding.tools import CodingTools


class TestCodingTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "hello.py").write_text(
            "def hello():\n    return 'world'\n\n# TODO: improve\n",
            encoding="utf-8",
        )
        (self.root / "src" / "util.py").write_text(
            "def add(a, b):\n    return a + b\n",
            encoding="utf-8",
        )
        (self.root / "README.md").write_text("# demo\n", encoding="utf-8")
        self.tools = CodingTools(self.root, max_output_chars=100)

    def tearDown(self):
        self.tmp.cleanup()

    def _call(self, name, **kwargs):
        return ToolCall(id="call_1", name=name, arguments=kwargs)

    def test_read_file_success(self):
        result = self.tools.execute(self._call("read_file", path="src/hello.py"))
        self.assertFalse(result.error)
        self.assertIn("def hello", result.content)
        self.assertEqual(result.metadata["path"], "src/hello.py")

    def test_read_file_path_traversal(self):
        result = self.tools.execute(self._call("read_file", path="../../etc/passwd"))
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "permission")

    def test_read_file_missing(self):
        result = self.tools.execute(self._call("read_file", path="src/nope.py"))
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "execution")

    def test_read_file_missing_arg(self):
        result = self.tools.execute(self._call("read_file"))
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "invalid_args")

    def test_read_file_truncate(self):
        big = "x" * 500
        (self.root / "big.txt").write_text(big, encoding="utf-8")
        result = self.tools.execute(self._call("read_file", path="big.txt"))
        self.assertFalse(result.error)
        self.assertTrue(result.metadata["truncated"])
        self.assertIn("省略", result.content)
        self.assertLessEqual(len(result.content), 100 + 20)

    def test_list_dir(self):
        result = self.tools.execute(self._call("list_dir", path="."))
        self.assertFalse(result.error)
        self.assertIn("[dir] src", result.content)
        self.assertIn("[file] README.md", result.content)

    def test_glob_py(self):
        result = self.tools.execute(self._call("glob", pattern="**/*.py", path="."))
        self.assertFalse(result.error)
        self.assertIn("src/hello.py", result.content)
        self.assertIn("src/util.py", result.content)

    def test_grep_basic(self):
        result = self.tools.execute(self._call("grep", query="TODO", path="."))
        self.assertFalse(result.error)
        self.assertIn("src/hello.py:4:", result.content)
        self.assertIn("TODO", result.content)

    def test_grep_regex(self):
        result = self.tools.execute(
            self._call("grep", query=r"def\s+add", path="src", regex=True)
        )
        self.assertFalse(result.error)
        self.assertIn("src/util.py:1:", result.content)

    def test_unknown_tool(self):
        result = self.tools.execute(self._call("delete_everything"))
        self.assertTrue(result.error)
        self.assertEqual(result.error_type, "invalid_args")

    def test_get_schemas(self):
        schemas = self.tools.get_schemas()
        names = [item["function"]["name"] for item in schemas]
        # memory 是第 12 个工具，recall_history 是第 13 个（MEMORY_ENABLED 默认开启）
        self.assertEqual(names, ["read_file", "list_dir", "glob", "grep",
                                         "write_file", "edit_file", "run_test", "run_bash",
                                         "load_skill", "search_skills",
                                         "memory", "recall_history"])


if __name__ == "__main__":
    unittest.main()
