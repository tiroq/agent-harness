from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from task_harness import issue_role_checkpoints as checks
from task_harness.core.role_orchestration import GitObservation
from task_harness.issue_execution import IssueExecutionError, _guard, _load, _save

HEAD = "a" * 40
DIFF = "d" * 64
PARENT = str(uuid.UUID("d57092c0-7466-4a71-8664-a96d43f10b10"))
RUN = "20261014T000000Z-fake0001"


@pytest.fixture()
def parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "agent-harness"
    wt = root / ".worktree" / "issue-1"
    wt.mkdir(parents=True)
    artifacts = root / ".copilot-workflow" / "runs" / RUN
    artifacts.mkdir(parents=True)
    with _guard(root, 1) as path:
        _save(path, {
            "issue": 1, "repo": "tiroq/agent-harness", "pr": 2, "head": HEAD,
            "worktree": str(wt), "run_id": RUN, "session_id": PARENT,
            "status": "RUNNING", "pid": 123456, "pid_start": "test", "boot_id": "boot",
        })
    monkeypatch.setattr(checks, "_same_process", lambda _state: True)
    observe = [GitObservation(HEAD, "", ())]
    class FakeGit:
        def __init__(self, worktree):
            assert worktree == wt
        def observe(self):
            return observe[0]
    monkeypatch.setattr(checks, "LocalGitEvidence", FakeGit)
    return root, artifacts, observe


def _report(artifacts: Path, *, name="implementer.md", role="implementer",
            session="subagent-implementer-001", verdict="PASS"):
    file = artifacts / name
    file.write_text(
        f"ROLE: {role}\n"
        f"GENERATED_BY_ROLE: {role}\n"
        f"AGENT_SESSION_ID: {session}\n"
        f"VERDICT: {verdict}\n"
        "SUMMARY: synthetic internal Copilot subagent response\n",
        encoding="utf-8",
    )
    return name


def test_internal_subagents_use_same_parent_ledger_no_copilot_launch(parent, monkeypatch):
    root, artifacts, observed = parent
    # A checkpoint must never launch its own parent/role Copilot subprocess.
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: pytest.fail("unexpected subprocess"))
    created = checks.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT,
                                subagent_id="subagent-implementer-001")
    assert created["role"] == "implementer"
    assert created["identity_source"] == "ORCHESTRATOR_ASSERTED_UNVERIFIED"
    observed[0] = GitObservation(HEAD, DIFF, ("src/a.py",),
                                  path_fingerprints=(("src/a.py", "hash"),))
    result = checks.role_complete(
        root, 1, run_id=RUN, parent_session_id=PARENT, report=_report(artifacts),
    )
    assert result["phase"] == "REVIEW"
    assert result["pr_ready"] is False
    assert result["subagent_id"] == "subagent-implementer-001"
    review = checks.role_begin(
        root, 1, run_id=RUN, parent_session_id=PARENT,
        subagent_id="subagent-reviewer-002",
    )
    assert review["role"] == "reviewer"
    next_result = checks.role_complete(
        root, 1, run_id=RUN, parent_session_id=PARENT,
        report=_report(artifacts, name="review.md", role="reviewer",
                       session="subagent-reviewer-002"),
    )
    assert next_result["phase"] == "INDEPENDENT_TEST"
    ledger = _load(root / ".copilot-workflow" / "issue-runs" / "issue-1.json")
    assert ledger["run_id"] == RUN
    assert ledger["session_id"] == PARENT
    assert ledger["status"] == "RUNNING"
    assert len(ledger["role_checkpoint"]["pipeline"]["history"]) == 2
    state = checks.role_status(root, 1)
    assert state["accepted_roles"] == 2
    assert state["identity_source"] == "ORCHESTRATOR_ASSERTED_UNVERIFIED"
    assert not state["pr_ready"]


def test_role_report_id_mismatch_rejected_without_advancing(parent):
    root, artifacts, observed = parent
    checks.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT,
                      subagent_id="subagent-implementer-001")
    observed[0] = GitObservation(HEAD, DIFF, ("src/a.py",))
    with pytest.raises(IssueExecutionError, match="provenance"):
        checks.role_complete(root, 1, run_id=RUN, parent_session_id=PARENT,
                             report=_report(artifacts, session="subagent-other-999"))
    current = checks.role_status(root, 1)
    assert current["phase"] == "IMPLEMENT"
    assert current["accepted_roles"] == 0
    assert current["status"] == "ROLE_PENDING"


def test_role_checkpoint_refuses_duplicate_or_parent_reused_subagent_id(parent):
    root, _, _ = parent
    with pytest.raises(IssueExecutionError, match="parent-reused"):
        checks.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT, subagent_id=PARENT)
    checks.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT,
                      subagent_id="subagent-implementer-001")
    with pytest.raises(IssueExecutionError, match="unfinished"):
        checks.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT,
                          subagent_id="subagent-reviewer-002")


def test_role_checkpoint_requires_live_same_parent_session(parent, monkeypatch):
    root, _, _ = parent
    with pytest.raises(IssueExecutionError, match="live owning"):
        checks.role_begin(root, 1, run_id=RUN, parent_session_id="wrong",
                          subagent_id="subagent-implementer-001")
    monkeypatch.setattr(checks, "_same_process", lambda _state: False)
    with pytest.raises(IssueExecutionError, match="live owning"):
        checks.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT,
                          subagent_id="subagent-implementer-001")


def test_report_path_and_duplicate_provenance_fail_closed(parent):
    root, artifacts, observed = parent
    checks.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT,
                      subagent_id="subagent-implementer-001")
    observed[0] = GitObservation(HEAD, DIFF, ("src/a.py",))
    with pytest.raises(IssueExecutionError, match="simple .md"):
        checks.role_complete(root, 1, run_id=RUN, parent_session_id=PARENT,
                             report="../escape.md")
    text = (
        "ROLE: implementer\nROLE: reviewer\nGENERATED_BY_ROLE: implementer\n"
        "AGENT_SESSION_ID: subagent-implementer-001\nVERDICT: PASS\n"
    )
    (artifacts / "bad.md").write_text(text)
    with pytest.raises(IssueExecutionError, match="duplicate role report"):
        checks.role_complete(root, 1, run_id=RUN, parent_session_id=PARENT, report="bad.md")
    (artifacts / "symlink.md").symlink_to(artifacts / "bad.md")
    with pytest.raises(IssueExecutionError, match="regular file"):
        checks.role_complete(root, 1, run_id=RUN, parent_session_id=PARENT, report="symlink.md")


def test_status_without_ledger_remains_read_only(tmp_path: Path):
    root = tmp_path / "empty-repo"
    result = checks.role_status(root, 1)
    assert result["status"] == "NOT_STARTED"
    assert not (root / ".copilot-workflow").exists()


def test_quality_gate_unknown_blocks_pre_audit_instead_of_fabricating_green(parent):
    root, artifacts, observed = parent
    roles = [
        ("implementer", "subagent-implementer-001", DIFF),
        ("reviewer", "subagent-reviewer-002", DIFF),
        ("test-engineer", "subagent-tester-003", DIFF),
    ]
    for role, subagent, diff in roles:
        opened = checks.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT,
                                   subagent_id=subagent)
        assert opened["role"] == role
        observed[0] = GitObservation(HEAD, diff, ("src/a.py",),
                                     path_fingerprints=(("src/a.py", "same"),))
        name = _report(artifacts, name=role + ".md", role=role, session=subagent)
        checks.role_complete(root, 1, run_id=RUN, parent_session_id=PARENT, report=name)
    assert checks.role_status(root, 1)["phase"] == "PRE_COMMIT_AUDIT"
    with pytest.raises(IssueExecutionError, match="mandatory quality"):
        checks.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT,
                          subagent_id="subagent-auditor-004")
    assert checks.role_status(root, 1)["status"] == "CHECKPOINTED"
