"""Real Git and fixed, pre-commit quality gate evidence (no Copilot charge)."""

from pathlib import Path
import subprocess

import pytest

from task_harness import issue_role_checkpoints as checkpoints
from task_harness.issue_execution import IssueExecutionError, _guard, _load, _save
from task_harness.issue_quality import quality_check
from task_harness.core.delivery_pipeline import PipelineState, PipelinePhase
from task_harness.core.role_orchestration import LocalGitEvidence


RUN = "20261014T010000Z-quality01"
PARENT = "531a95da-4d71-43c9-8f17-fdb8af639b69"


def _git(cwd, *args):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


@pytest.fixture()
def ready(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    wt = root / ".worktree" / "issue-1"
    wt.mkdir(parents=True)
    _git(wt, "init", "-q")
    _git(wt, "config", "user.name", "Test")
    _git(wt, "config", "user.email", "unit@test.invalid")
    scripts = wt / "scripts"
    scripts.mkdir()
    gate = scripts / "quality-gate.sh"
    gate.write_text("#!/usr/bin/env bash\nset -e\nexit 0\n")
    (wt / "src").mkdir()
    (wt / "src" / "item.py").write_text("value = 1\n")
    _git(wt, "add", "scripts/quality-gate.sh", "src/item.py")
    _git(wt, "commit", "-qm", "baseline")
    (wt / "src" / "item.py").write_text("value = 2\n")
    observation = LocalGitEvidence(wt).observe()
    pipeline = PipelineState.start(
        repository="tiroq/agent-harness", issue=1, pr=2,
        head_sha=observation.head_sha, diff_sha=observation.diff_sha,
    )
    pipeline.phase = PipelinePhase.PRE_COMMIT_AUDIT
    monkeypatch.setattr(checkpoints, "_same_process", lambda ledger: True)
    with _guard(root, 1) as path:
        _save(path, {
            "issue": 1, "repo": "tiroq/agent-harness", "pr": 2,
            "head": observation.head_sha, "worktree": str(wt),
            "run_id": RUN, "session_id": PARENT, "status": "RUNNING",
            "pid": 789, "pid_start": "test", "boot_id": "test",
            "role_checkpoint": {
                "pipeline": checkpoints._snapshot(pipeline),
                "before": None,
                "identity_source": "ORCHESTRATOR_ASSERTED_UNVERIFIED",
            },
        })
    return root, wt, gate, observation


def test_fixed_quality_gate_pass_is_sha_bound_and_unblocks_pre_audit(ready, monkeypatch):
    root, wt, _, before = ready
    monkeypatch.setenv("COPILOT_AUTOPILOT_AUTHORITATIVE_RUNNER", "sentinel")
    evidence = quality_check(root, 1, run_id=RUN, parent_session_id=PARENT)
    assert evidence["status"] == "PASS"
    assert evidence["exit_code"] == 0
    assert evidence["command"] == ["bash", "scripts/quality-gate.sh"]
    assert evidence["git_unchanged"] is True
    assert evidence["head_sha"] == before.head_sha
    assert evidence["diff_sha"] == before.diff_sha
    opened = checkpoints.role_begin(
        root, 1, run_id=RUN, parent_session_id=PARENT,
        subagent_id="subagent-auditor-0001",
    )
    assert opened["role"] == "evidence-auditor"
    ledger = _load(root / ".copilot-workflow" / "issue-runs" / "issue-1.json")
    assert ledger["role_checkpoint"]["quality"]["status"] == "PASS"


def test_known_failed_gate_refuses_auditor(ready):
    root, wt, gate, _ = ready
    gate.write_text("#!/usr/bin/env bash\nexit 9\n")
    _git(wt, "add", "scripts/quality-gate.sh")
    _git(wt, "commit", "-qm", "update baseline check")
    # The model/worktree state remains bound to an older HEAD; always reject.
    with pytest.raises(IssueExecutionError, match="quality gate baseline changed"):
        quality_check(root, 1, run_id=RUN, parent_session_id=PARENT)


def test_gate_script_changed_from_head_is_refused(ready):
    root, _, gate, _ = ready
    gate.write_text("#!/bin/bash\nexit 0\n")
    with pytest.raises(IssueExecutionError, match="not present unchanged"):
        quality_check(root, 1, run_id=RUN, parent_session_id=PARENT)


def test_quality_gate_does_not_accept_stale_diff_before_audit(ready):
    root, wt, _, _ = ready
    assert quality_check(root, 1, run_id=RUN, parent_session_id=PARENT)["status"] == "PASS"
    (wt / "src" / "item.py").write_text("value = 3\n")
    with pytest.raises(IssueExecutionError, match="mandatory quality evidence absent, red or stale"):
        checkpoints.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT,
                               subagent_id="subagent-auditor-0001")


def test_quality_gate_timeout_and_fail_cannot_be_reported_as_green(ready, monkeypatch):
    root, wt, gate, _ = ready
    import task_harness.issue_quality as quality
    original = quality.subprocess.run
    def fake_run(args, **kwargs):
        if args == ["bash", "scripts/quality-gate.sh"]:
            raise subprocess.TimeoutExpired(cmd=args, timeout=kwargs["timeout"])
        return original(args, **kwargs)
    monkeypatch.setattr(quality.subprocess, "run", fake_run)
    result = quality_check(root, 1, run_id=RUN, parent_session_id=PARENT, timeout_sec=1)
    assert result["status"] == "FAIL"
    assert result["timed_out"] is True
    with pytest.raises(IssueExecutionError, match="mandatory quality evidence"):
        checkpoints.role_begin(root, 1, run_id=RUN, parent_session_id=PARENT,
                               subagent_id="subagent-auditor-0001")


def test_quality_gate_must_have_exact_parent_live_pid(ready, monkeypatch):
    root, _, _, _ = ready
    monkeypatch.setattr(checkpoints, "_same_process", lambda ledger: False)
    with pytest.raises(IssueExecutionError, match="live owning"):
        quality_check(root, 1, run_id=RUN, parent_session_id=PARENT)


def test_quality_gate_argv_is_literal_and_sanitizes_internal_env(ready, monkeypatch):
    root, wt, gate, _ = ready
    # Rewrite committed gate as a script that rejects inherited control flags.
    gate.write_text(
        "#!/usr/bin/env bash\n"
        "if [ -n \"${COPILOT_AUTOPILOT_AUTHORITATIVE_RUNNER:-}\" ]; then exit 7; fi\n"
        "exit 0\n"
    )
    _git(wt, "add", "scripts/quality-gate.sh")
    _git(wt, "commit", "-qm", "enforce normalized environment")
    observation = LocalGitEvidence(wt).observe()
    with _guard(root, 1) as path:
        ledger = _load(path)
        ledger["head"] = observation.head_sha
        pipeline = checkpoints._checkpoint_state(ledger["role_checkpoint"])
        pipeline.base_head_sha = observation.head_sha
        pipeline.current_head_sha = observation.head_sha
        pipeline.revision_diff_sha = observation.diff_sha
        ledger["role_checkpoint"]["pipeline"] = checkpoints._snapshot(pipeline)
        _save(path, ledger)
    monkeypatch.setenv("COPILOT_AUTOPILOT_AUTHORITATIVE_RUNNER", "1")
    assert quality_check(root, 1, run_id=RUN, parent_session_id=PARENT)["status"] == "PASS"


def test_precommit_auditor_requires_observed_hook_and_matching_quality(ready):
    import json
    from task_harness.issue_subagent_hooks import ingest_hook_event
    root, wt, _, _ = ready
    assert quality_check(root, 1, run_id=RUN, parent_session_id=PARENT)["status"] == "PASS"
    agent_id = "subagent-auditor-0001"
    opened = checkpoints.role_begin(
        root, 1, run_id=RUN, parent_session_id=PARENT, subagent_id=agent_id,
    )
    assert opened["role"] == "evidence-auditor"
    artifact = root / ".copilot-workflow" / "runs" / RUN
    artifact.mkdir(parents=True)
    (artifact / "audit.md").write_text(
        f"ROLE: evidence-auditor\nGENERATED_BY_ROLE: evidence-auditor\n"
        f"AGENT_SESSION_ID: {agent_id}\nVERDICT: PASS\n"
        "APPROVED_TASK_PATHS: src/item.py\n"
    )
    with pytest.raises(IssueExecutionError, match="requires observed Copilot subagentStop"):
        checkpoints.role_complete(
            root, 1, run_id=RUN, parent_session_id=PARENT,
            report="audit.md", subagent_id=agent_id,
        )
    base = {
        "sessionId": PARENT, "cwd": str(wt),
        "agentName": "task-harness-evidence-auditor",
        "timestamp": 1791950000000,
    }
    ingest_hook_event(root, 1, run_id=RUN, parent_session_id=PARENT,
                      event_name="subagentStart", raw=json.dumps(base).encode())
    ingest_hook_event(
        root, 1, run_id=RUN, parent_session_id=PARENT,
        event_name="subagentStop",
        raw=json.dumps(dict(base, agentId=agent_id, agentType="custom",
                            response="completed", stopReason="end_turn")).encode(),
    )
    completed = checkpoints.role_complete(
        root, 1, run_id=RUN, parent_session_id=PARENT,
        report="audit.md", subagent_id=agent_id,
    )
    assert completed["phase"] == "COMMIT"
    assert completed["identity_source"] == "COPILOT_HOOK_OBSERVED_UNATTESTED"
    assert completed["pr_ready"] is False
