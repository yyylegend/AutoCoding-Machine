"""WorkspaceSandbox 测试。"""

import unittest
from pathlib import Path

from src.profiles.coding.sandbox import WorkspaceSandbox


class TestWorkspaceSandbox(unittest.TestCase):
    def setUp(self):
        self.root = Path.cwd()
        self.sandbox = WorkspaceSandbox(self.root)

    def test_resolve_relative_inside(self):
        target = self.sandbox.resolve("src/engine/contracts.py")
        self.assertIsNotNone(target)
        self.assertTrue(str(target).endswith("contracts.py"))

    def test_resolve_path_traversal(self):
        target = self.sandbox.resolve("../../etc/passwd")
        self.assertIsNone(target)

    def test_resolve_empty_path(self):
        self.assertIsNone(self.sandbox.resolve(""))
        self.assertIsNone(self.sandbox.resolve("   "))

    def test_is_inside(self):
        self.assertTrue(self.sandbox.is_inside("src"))
        self.assertFalse(self.sandbox.is_inside("../.."))

    def test_relpath(self):
        full = (self.root / "src" / "engine" / "contracts.py").resolve()
        rel = self.sandbox.relpath(full)
        self.assertEqual(rel.replace("\\", "/"), "src/engine/contracts.py")


if __name__ == "__main__":
    unittest.main()
