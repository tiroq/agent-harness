"""Explicit, bounded mandatory quality gate for one parent Issue session.

A model report cannot create PASS evidence. Only the configured, unchanged
repository quality gate command can produce a SHA/diff-bound local PASS.
This is NOT remote CI or PR readiness authority.
"""

from __future__ import annotations

import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

from .core.delivery_pipeline import PipelinePhase
from .core.role_orchestration import LocalGitEvidence
from .issue_execution import IssueExecutionError, _clock, _guard, _load, _save
from .issue_role_checkpoints import _checkpoint_state, _parent, _git

_TIMEOUT_SEC = 180
_QUALITY_PATH = "scripts/quality-gate.sh"
_INTERNAL_ENV_PREFIXES = ("HARNESS_", "COPILOT_AUTOPILOT_", "WORKFLOW_")
_COMMAND = ("bash", _QUALITY_PATH)


def _script_is_authoritative(worktree: Path) -> None:
    file = worktree / _QUALITY_PATH
    if not file.is_file() or file.is_symlink():
        raise IssueExecutionError("committed quality-gate.sh is missing or unsafe")
    try:
        exists = subprocess.run(
            ["git", "cat-file", "-e", "HEAD:" + _QUALITY_PATH],
            cwd=worktree, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=False,
        )
        clean = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", _QUALITY_PATH],
            cwd=worktree, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IssueExecutionError("could not check quality gate script provenance") from exc
    if exists.returncode or clean.returncode:
        raise IssueExecutionError("quality gate script is not present unchanged in HEAD")


def quality_check(
    root: Path, issue: int, *, run_id: str, parent_session_id: str,
    timeout_sec: int = _TIMEOUT_SEC,
) -> dict[str, Any]:
    """Execute a fixed committed script, never shell text from an agent."""
    if not 1 <= timeout_sec <= _TIMEOUT_SEC:
        raise IssueExecutionError("unsupported quality-gate timeout")
    nonce = secrets.token_hex(12)
    with _guard(root, issue) as path:
        ledger = _parent(_load(path), issue=issue, run_id=run_id, session_id=parent_session_id)
        info = ledger.get("role_checkpoint")
        if not isinstance(info, dict):
            raise IssueExecutionError("quality gate requires active role pipeline")
        state = _checkpoint_state(info)
        if state.phase != PipelinePhase.PRE_COMMIT_AUDIT or state.pending is not None:
            raise IssueExecutionError("quality gate only valid between independent test and pre-audit")
        worktree = Path(ledger["worktree"]).resolve()
        _script_is_authoritative(worktree)
        observation = _git(ledger, root, issue)
        if observation.head_sha != state.current_head_sha or observation.diff_sha != state.revision_diff_sha:
            raise IssueExecutionError("quality gate baseline changed")
        if info.get("quality_pending") is not None:
            raise IssueExecutionError("quality gate already running or orphaned")
        info["quality_pending"] = nonce
        info.pop("quality", None)
        _save(path, ledger)

    # Do not hold the Issue ledger lock while running tests: cancellation,
    # status and hook event callbacks must not be blocked for 3 minutes.
    # No shell=True, dynamically constructed argv, or inherited control flags.
    env = {
        k: v for k, v in os.environ.items()
        if not any(k.startswith(prefix) for prefix in _INTERNAL_ENV_PREFIXES)
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    timed_out = False
    exit_code: int | None = None
    try:
        result = subprocess.run(
            list(_COMMAND), cwd=worktree, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=env, check=False, timeout=timeout_sec,
        )
        exit_code = result.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
    except OSError:
        exit_code = None

    with _guard(root, issue) as path:
        ledger = _parent(_load(path), issue=issue, run_id=run_id, session_id=parent_session_id)
        info = ledger.get("role_checkpoint")
        if not isinstance(info, dict) or info.get("quality_pending") != nonce:
            raise IssueExecutionError("quality check state changed during execution")
        state = _checkpoint_state(info)
        after = _git(ledger, root, issue)
        matches = (
            state.phase == PipelinePhase.PRE_COMMIT_AUDIT and state.pending is None
            and after.head_sha == observation.head_sha
            and after.diff_sha == observation.diff_sha
        )
        status = "PASS" if exit_code == 0 and not timed_out and matches else "FAIL"
        evidence = {
            "name": "committed_quality_gate",
            "status": status, "command": list(_COMMAND),
            "exit_code": exit_code, "timed_out": timed_out,
            "head_sha": observation.head_sha, "diff_sha": observation.diff_sha,
            "run_id": run_id, "parent_session_id": parent_session_id,
            "git_unchanged": matches, "checked_at": _clock(),
        }
        info["quality"] = evidence
        info.pop("quality_pending")
        _save(path, ledger)
        return dict(evidence, pr_ready=False)
