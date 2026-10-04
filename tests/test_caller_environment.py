import os
import subprocess
import sys
from pathlib import Path


def test_settings_loads_model_config_from_callers_working_directory(tmp_path):
    (tmp_path / ".env").write_text(
        "CODING_LLM_MODEL=caller-test-model\nCODING_CONTEXT_LENGTH=4096\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    for name in ("LLM_MODEL", "CODING_LLM_MODEL", "CODING_CONTEXT_LENGTH"):
        environment.pop(name, None)
    source_root = str(Path(__file__).resolve().parents[1] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (
        source_root, environment.get("PYTHONPATH"),
    )))

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from auto_coding_machine.config.settings import settings; "
            "print(settings.CODING_LLM_MODEL); "
            "print(settings.CODING_CONTEXT_LENGTH)",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )

    assert result.stdout.splitlines() == ["caller-test-model", "4096"]
