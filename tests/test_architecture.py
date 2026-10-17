from pathlib import Path

from importlinter.cli import EXIT_STATUS_SUCCESS, lint_imports

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def test_imports_follow_dependency_direction():
    assert lint_imports(config_filename=str(PYPROJECT), no_logo=True) == EXIT_STATUS_SUCCESS
