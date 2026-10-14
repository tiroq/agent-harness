"""Hermetic Copilot CLI hook ingestion; no paid Copilot process is launched."""

import json
import uuid
from pathlib import Path

import pytest

from task_harness import issue_role_checkpoints as checkpoints
from task_harness import issue_subagent_hooks as hooks
from task_harness.core.role_orchestration import GitObservation
from task_harness.issue_execution import IssueExecutionError, _guard, _save

ROOT_RUN = "20261014T010000Z-hooktest01"
PARENT = "1e495143-a3f5-4cd8-baa7-a540255c74a0"
AGENT = "subagent-implementer-001"
HEAD = "a" * 40


@pytest.fixture()
def bound(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    wt = root / ".worktree" / "issue-1"
    wt.mkdir(parents=True)
    (root / ".copilot-workflow" / "runs" / ROOT_RUN).mkdir(parents=True)
    with _guard(root, 1) as file:
        _save(file, {
            "issue": 1, "repo": "tiroq/agent-harness",
            "pr": 2, "head": HEAD, "worktree": str(wt),
            "run_id": ROOT_RUN, "session_id": PARENT,
            "status": "RUNNING", "pid": 987, "boot_id": "fake", "pid_start": "fake",
        })
    monkeypatch.setattr(checkpoints, "_same_process", lambda _: True)
    class FakeGit:
        def __init__(self, worktree):
            assert worktree == wt
        def observe(self):
            return GitObservation(HEAD, "", ())
    monkeypatch.setattr(checkpoints, "LocalGitEvidence", FakeGit)
    checkpoints.role_begin(root, 1, run_id=ROOT_RUN, parent_session_id=PARENT)
    return root, wt


def send(root, wt, kind, *, agent_id=AGENT, agent_name="task-harness-implementer",
         session=PARENT, cwd=None, stop_reason="end_turn"):
    event = {
        "sessionId": session, "timestamp": 1791950000000,
        "cwd": str(cwd or wt), "transcriptPath": "/untrusted/transcript",
        "agentName": agent_name,
    }
    if kind == "subagentStop":
        event.update(agentId=agent_id, agentType="custom", response="ROLE: implementer",
                     stopReason=stop_reason)
    return hooks.ingest_hook_event(
        root, 1, run_id=ROOT_RUN, parent_session_id=PARENT,
        event_name=kind, raw=json.dumps(event).encode(),
    )


def test_real_hook_start_stop_matches_checkpoint_without_new_parent(bound):
    root, wt = bound
    start = send(root, wt, "subagentStart")
    assert start["status"] == "HOOK_RECORDED"
    stopped = send(root, wt, "subagentStop")
    assert stopped["identity_source"] == "COPILOT_HOOK_OBSERVED_UNATTESTED"
    report = root / ".copilot-workflow" / "runs" / ROOT_RUN / "implementer.md"
    report.write_text(
        f"ROLE: implementer\nGENERATED_BY_ROLE: implementer\n"
        f"AGENT_SESSION_ID: {AGENT}\nVERDICT: PASS\n"
    )
    # The fake Git before/after snapshots match. This is still NOT PR_READY.
    accepted = checkpoints.role_complete(
        root, 1, run_id=ROOT_RUN, parent_session_id=PARENT,
        report="implementer.md", subagent_id=AGENT,
    )
    assert accepted["phase"] == "REVIEW"
    assert accepted["identity_source"] == "COPILOT_HOOK_OBSERVED_UNATTESTED"
    assert accepted["pr_ready"] is False
    assert checkpoints.role_status(root, 1)["identity_source"] == "COPILOT_HOOK_OBSERVED_UNATTESTED"


def test_hook_mismatch_never_accepts_self_claimed_role(bound):
    root, wt = bound
    send(root, wt, "subagentStart")
    send(root, wt, "subagentStop", agent_id="subagent-other-002")
    report = root / ".copilot-workflow" / "runs" / ROOT_RUN / "implementer.md"
    report.write_text(
        f"ROLE: implementer\nGENERATED_BY_ROLE: implementer\n"
        f"AGENT_SESSION_ID: {AGENT}\nVERDICT: PASS\n"
    )
    with pytest.raises(IssueExecutionError, match="does not match"):
        checkpoints.role_complete(
            root, 1, run_id=ROOT_RUN, parent_session_id=PARENT,
            report="implementer.md", subagent_id=AGENT,
        )
    assert checkpoints.role_status(root, 1)["accepted_roles"] == 0


@pytest.mark.parametrize("parameter,value,error", [
    ("session", "different-parent", "parent session"),
    ("agent_name", "task-harness-reviewer", "role does not match"),
    ("stop_reason", "abort", "did not end normally"),
])
def test_wrong_identity_or_reason_fails_closed(bound, parameter, value, error):
    root, wt = bound
    if parameter == "stop_reason":
        send(root, wt, "subagentStart")
    with pytest.raises(IssueExecutionError, match=error):
        send(root, wt, "subagentStop" if parameter == "stop_reason" else "subagentStart",
             **{parameter: value})


def test_hook_stop_requires_start_and_is_idempotent(bound):
    root, wt = bound
    with pytest.raises(IssueExecutionError, match="without matching start"):
        send(root, wt, "subagentStop")
    assert send(root, wt, "subagentStart")["status"] == "HOOK_RECORDED"
    assert send(root, wt, "subagentStart")["status"] == "DUPLICATE_IGNORED"
    assert send(root, wt, "subagentStop")["status"] == "HOOK_RECORDED"
    assert send(root, wt, "subagentStop")["status"] == "DUPLICATE_IGNORED"


def test_wrong_cwd_invalid_json_large_payload_and_unrelated_agent(bound):
    root, wt = bound
    with pytest.raises(IssueExecutionError, match="cwd mismatch"):
        send(root, wt, "subagentStart", cwd=root)
    assert send(root, wt, "subagentStart", agent_name="task-harness-explore")["status"] == "IGNORED_UNRELATED_AGENT"
    with pytest.raises(IssueExecutionError, match="invalid Copilot hook JSON"):
        hooks.ingest_hook_event(
            root, 1, run_id=ROOT_RUN, parent_session_id=PARENT,
            event_name="subagentStop", raw=b"{not json}",
        )
    with pytest.raises(IssueExecutionError, match="oversized"):
        hooks.ingest_hook_event(
            root, 1, run_id=ROOT_RUN, parent_session_id=PARENT,
            event_name="subagentStart", raw=b"a" * (2 * 1024 * 1024 + 1),
        )


def test_hook_config_contains_both_lifecycle_events(tmp_path):
    config = Path(__file__).resolve().parents[1] / ".github" / "hooks" / "task-harness.json"
    payload = json.loads(config.read_text())
    assert payload["version"] == 1
    for event in ("subagentStart", "subagentStop"):
        assert len(payload["hooks"][event]) == 1
        definition = payload["hooks"][event][0]
        assert definition["type"] == "command"
        assert "issue-hook-event" in definition["bash"]
        assert definition["timeoutSec"] <= 10


def test_init_installs_hook_template_without_overwriting_existing_project(tmp_path):
    from task_harness.cli import init_project
    project = tmp_path / "another-repository"
    assert init_project(project) == 0
    hook = project / ".github" / "hooks" / "task-harness.json"
    data = json.loads(hook.read_text())
    assert set(data["hooks"]) == {"subagentStart", "subagentStop"}
    hook.write_text('{"custom":"keep existing hook"}\n')
    assert init_project(project) == 0
    assert json.loads(hook.read_text()) == {"custom": "keep existing hook"}



def test_missing_hook_events_mean_no_observed_agent_for_legacy_checkpoint():
    # Role checkpoints existed before hooks were introduced and acquire the
    # list only when subagentStart is actually received.
    assert hooks.stopped_agent_id({}) is None
    assert hooks.stopped_agent_id({"before": {"head_sha": "abc"}}) is None
    assert hooks.stopped_agent_id({"hook_events": []}) is None


@pytest.mark.parametrize("invalid", [None, (), "[]", {}, 12])
def test_present_malformed_hook_ledger_is_still_rejected(invalid):
    # No fallback may silently downgrade corrupted evidence to "no hook".
    with pytest.raises(IssueExecutionError, match="malformed Copilot hook ledger"):
        hooks.stopped_agent_id({"hook_events": invalid})
