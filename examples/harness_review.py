"""Run a read-only code review through the public Harness entry point."""

import argparse
import sys
from pathlib import Path

from auto_coding_machine import (
    ModelAdapter,
    ProfileTools,
    load_profile,
    open_harness_session,
)


def review_workspace(workspace: str | Path, question: str) -> dict:
    workspace = Path(workspace).resolve()
    profile = load_profile("review")
    tools = ProfileTools(workspace, profile)
    model = ModelAdapter(tools.get_schemas(), model=profile.model)
    session = open_harness_session(
        workspace, model.call, profile=profile, tools=tools
    )
    return session.begin_run(question).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Review a project without write tools")
    parser.add_argument("workspace", type=Path, help="project directory to review")
    parser.add_argument("question", help="what the review should focus on")
    args = parser.parse_args()

    result = review_workspace(args.workspace, args.question)
    if result.get("status") != "success":
        print(f"Review 未完成：{result.get('status')}", file=sys.stderr)
        if result.get("status") == "permission_required":
            print("没有自动批准任何工具权限。", file=sys.stderr)
        return 1

    print(result.get("reply", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
