"""Hermetic Issue role lifecycle with real Git and simulated Copilot hook events.

This exercises the production Issue ledger/checkpoint/quality transitions,
without running any Copilot model or publishing a GitHub PR.
"""
from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path

import pytest

from task_harness import issue_role_checkpoints as checkpoints
from task_harness import issue_subagent_hooks as hooks
from task_harness.issue_execution import _guard, _load, _save
from task_harness.issue_quality import quality_check

RUN = "20261015T000000Z-typedrole01"
PARENT = str(uuid.UUID("d57092c0-7466-4a71-8664-a96d43f10b10"))


def git(worktree: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=worktree, check=True,
        capture_output=True, text=True, timeout=15,
    )
    return result.stdout.strip()


@pytest.fixture()
def issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "harness"
    worktree = root / ".worktree" / "issue-1"
    (worktree / "src").mkdir(parents=True)
    (worktree / "scripts").mkdir(parents=True)
    git(worktree, "init", "-q")
    git(worktree, "config", "user.name", "Harness Test")
    git(worktree, "config", "user.email", "harness-test@example.invalid")
    (worktree / "src" / "base.py").write_text("BASE = True\n")
    (worktree / "scripts" / "quality-gate.sh").write_text(
        "#!/bin/sh\nset -eu\ntest -f src/feature.py\n",
        encoding="utf-8",
    )
    git(worktree, "add", ".")
    git(worktree, "commit", "-qm", "baseline")
    base = git(worktree, "rev-parse", "HEAD")
    artifacts = root / ".copilot-workflow" / "runs" / RUN
    artifacts.mkdir(parents=True)
    with _guard(root, 1) as path:
        _save(path, {
            "run_id": RUN, "session_id": PARENT,
            "issue": 1, "repo": "tiroq/agent-harness",
            "pr": 2, "head": base, "worktree": str(worktree),
            "status": "RUNNING", "pid": 12345,
            "pid_start": "test", "boot_id": "test",
            "require_subagent_hooks": True,
        })
    # Simulate a verified LIVE parent identity; actual Git and quality
    # subprocess operations remain untouched.
    monkeypatch.setattr(checkpoints, "_same_process", lambda state: True)
    return root, worktree, artifacts, base


def role(
    root: Path, worktree: Path, artifacts: Path, *,
    expected_role: str, agent_id: str,
    report_name: str, approved_paths: str = "",
) -> str:
    opened = checkpoints.role_begin(
        root, 1, run_id=RUN, parent_session_id=PARENT,
    )
    assert opened["role"] == expected_role
    assert opened["status"] == "ROLE_PENDING"
    for event in ("subagentStart", "subagentStop"):
        payload = {
            "sessionId": PARENT, "cwd": str(worktree),
            "agentName": "task-harness-" + expected_role,
        }
        if event == "subagentStop":
            payload.update(agentId=agent_id, stopReason="end_turn")
        recorded = hooks.ingest_hook_event(
            root, 1, run_id=RUN, parent_session_id=PARENT,
            event_name=event, raw=json.dumps(payload).encode(),
        )
        assert recorded["status"] == "HOOK_RECORDED"
    fields = [
        f"ROLE: {expected_role}",
        f"GENERATED_BY_ROLE: {expected_role}",
        f"AGENT_SESSION_ID: {agent_id}",
        "VERDICT: PASS",
    ]
    if approved_paths:
        fields.append(f"APPROVED_TASK_PATHS: {approved_paths}")
    (artifacts / report_name).write_text(
        "\n".join(fields) + "\n", encoding="utf-8",
    )
    accepted = checkpoints.role_complete(
        root, 1, run_id=RUN, parent_session_id=PARENT,
        report=report_name, subagent_id=agent_id,
    )
    assert accepted["identity_source"] == "COPILOT_HOOK_OBSERVED_UNATTESTED"
    assert accepted["pr_ready"] is False
    return accepted["phase"]


def test_complete_six_role_flow_remains_local_audited_not_pr_ready(issue):
    root, worktree, artifacts, base = issue
    (worktree / "src" / "feature.py").write_text("FEATURE = True\n")
    # Implementer checkpoint must capture pre-change Git state.
    # Rewind the new file for role_begin; mutation occurs between start
    # and stop in a real invocation, as simulated below.
    (worktree / "src" / "feature.py").unlink()

    opened = checkpoints.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT)
    assert opened["role"] == "implementer"
    (worktree / "src" / "feature.py").write_text("FEATURE = True\n")
    for event in ("subagentStart", "subagentStop"):
        payload = {
            "sessionId": PARENT, "cwd": str(worktree),
            "agentName": "task-harness-implementer",
        }
        if event == "subagentStop":
            payload.update(agentId="subagent-implementer-0001", stopReason="end_turn")
        hooks.ingest_hook_event(
            root, 1, run_id=RUN, parent_session_id=PARENT,
            event_name=event, raw=json.dumps(payload).encode(),
        )
    (artifacts / "implementer.md").write_text(
        "ROLE: implementer\nGENERATED_BY_ROLE: implementer\n"
        "AGENT_SESSION_ID: subagent-implementer-0001\nVERDICT: PASS\n",
        encoding="utf-8",
    )
    implement = checkpoints.role_complete(
        root, 1, run_id=RUN, parent_session_id=PARENT,
        report="implementer.md", subagent_id="subagent-implementer-0001",
    )
    assert implement["phase"] == "REVIEW"
    assert role(root, worktree, artifacts, expected_role="reviewer",
                agent_id="subagent-reviewer-0002", report_name="reviewer.md") == "INDEPENDENT_TEST"
    assert role(root, worktree, artifacts, expected_role="test-engineer",
                agent_id="subagent-tester-0003", report_name="tester.md") == "PRE_COMMIT_AUDIT"

    with pytest.raises(Exception, match="mandatory quality evidence"):
        checkpoints.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT)
    gate = quality_check(root, 1, run_id=RUN, parent_session_id=PARENT)
    assert gate["status"] == "PASS"
    assert gate["git_unchanged"] is True
    assert role(root, worktree, artifacts, expected_role="evidence-auditor",
                agent_id="subagent-preauditor-0004", report_name="preaudit.md",
                approved_paths="src/feature.py") == "COMMIT"

    # A real committer updates HEAD once and exactly the auditor-approved path.
    opened = checkpoints.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT)
    assert opened["role"] == "committer"
    git(worktree, "add", "src/feature.py")
    git(worktree, "commit", "-qm", "implement feature")
    for event in ("subagentStart", "subagentStop"):
        payload = {
            "sessionId": PARENT, "cwd": str(worktree),
            "agentName": "task-harness-committer",
        }
        if event == "subagentStop":
            payload.update(agentId="subagent-committer-0005", stopReason="end_turn")
        hooks.ingest_hook_event(
            root, 1, run_id=RUN, parent_session_id=PARENT,
            event_name=event, raw=json.dumps(payload).encode(),
        )
    (artifacts / "committer.md").write_text(
        "ROLE: committer\nGENERATED_BY_ROLE: committer\n"
        "AGENT_SESSION_ID: subagent-committer-0005\nVERDICT: PASS\n",
        encoding="utf-8",
    )
    committed = checkpoints.role_complete(
        root, 1, run_id=RUN, parent_session_id=PARENT,
        report="committer.md", subagent_id="subagent-committer-0005",
    )
    assert committed["phase"] == "POST_COMMIT_AUDIT"
    assert git(worktree, "rev-parse", "HEAD^") == base
    assert git(worktree, "status", "--porcelain") == ""
    assert role(root, worktree, artifacts, expected_role="evidence-auditor",
                agent_id="subagent-postauditor-0006", report_name="postaudit.md") == "LOCAL_AUDITED"
    status = checkpoints.role_status(root, 1)
    assert status["phase"] == "LOCAL_AUDITED"
    assert status["accepted_roles"] == 6
    assert status["pr_ready"] is False
    ledger = _load(root / ".copilot-workflow" / "issue-runs" / "issue-1.json")
    assert ledger["run_id"] == RUN
    assert ledger["session_id"] == PARENT
    assert ledger["status"] == "RUNNING"
    assert ledger["require_subagent_hooks"] is True
    with _guard(root, 1) as path:
        terminal = _load(path)
        assert terminal is not None
        terminal["status"] = "SESSION_EXITED"
        terminal["exit_code"] = 0
        _save(path, terminal)
    from task_harness.issue_execution import issue_status
    report = issue_status(root, 1)
    assert report["role_phase"] == "LOCAL_AUDITED"
    assert report["accepted_roles"] == 6
    assert report["pending_role"] is None
    assert report["delivery_verdict"] == "LOCAL_AUDITED_UNPUBLISHED"
    assert report["pr_ready"] is False
