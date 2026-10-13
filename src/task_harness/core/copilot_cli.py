"""Shared, repository-neutral Copilot CLI invocation policy."""

from __future__ import annotations

import os


def copilot_arguments(*, model: str, allow_all: bool) -> list[str]:
    """Shared Copilot CLI policy for package and GitHub Issue entrypoints."""
    args = [
        "copilot", "--agent=task-harness-orchestrator", "--model", model,
        "--autopilot",
        f"--max-autopilot-continues={os.getenv('COPILOT_MAX_AUTOPILOT_CONTINUES', '100')}",
    ]
    if allow_all:
        args.append("--allow-all-tools")
    for denied in (
        "git push", "git merge", "git rebase", "git reset", "git clean",
        "git checkout", "git switch", "rm -rf", "sudo", "su", "chown",
        "chmod -R", "pip install", "uv pip install", "npm install",
    ):
        args.append(f"--deny-tool=shell({denied})")
    return args


