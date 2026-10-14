from __future__ import annotations

import pytest

from task_harness.core.delivery_pipeline import (
    PipelineError, PipelinePhase, PipelineState, PipelineVerdict, RoleEvidence,
)
from task_harness.core.models import (
    DeliveryRole, PrerequisiteCheck, PrerequisiteStatus, RoleReport,
)

HEAD = "a" * 40
COMMIT = "c" * 40
DIFF = "d" * 64
UPDATED = "e" * 64
PASSED = (
    PrerequisiteCheck("pytest", PrerequisiteStatus.PASS),
    PrerequisiteCheck("ruff", PrerequisiteStatus.PASS),
)


def new() -> PipelineState:
    return PipelineState.start(
        repository="tiroq/agent-harness", issue=1, pr=2,
        head_sha=HEAD, diff_sha=DIFF,
    )


def ticket(state: PipelineState, *, quality=()):
    return state.request(
        run_id=f"run-{len(state.history)}", session_id=f"session-{len(state.history)}",
        head_sha=state.current_head_sha, diff_sha=state.revision_diff_sha, quality=quality,
    )


def report(t, *, verdict=PipelineVerdict.PASS, diff=None, head=None,
           paths=(), committed=(), parent=None, quality=()):
    return RoleEvidence(
        role_report=RoleReport(
            role=t.role.value, generated_by_role=t.role.value,
            agent_session_id=t.session_id,
        ),
        verdict=verdict, run_id=t.run_id, session_id=t.session_id,
        head_before=t.head_sha, diff_before=t.diff_sha,
        head_after=t.head_sha if head is None else head,
        diff_after=t.diff_sha if diff is None else diff,
        approved_paths=paths, committed_paths=committed,
        commit_parent_sha=parent, quality=quality,
    )


def to_auditor(state):
    t=ticket(state)
    assert t.role is DeliveryRole.IMPLEMENTER
    assert state.accept(report(t, diff=UPDATED)) is PipelinePhase.REVIEW
    t=ticket(state)
    assert t.role is DeliveryRole.REVIEWER
    assert state.accept(report(t)) is PipelinePhase.INDEPENDENT_TEST
    t=ticket(state)
    assert t.role is DeliveryRole.TEST_ENGINEER
    assert state.accept(report(t)) is PipelinePhase.PRE_COMMIT_AUDIT


def test_full_independent_sequence_stops_at_local_audited_not_pr_ready():
    state=new()
    to_auditor(state)
    t=ticket(state, quality=PASSED)
    assert t.role is DeliveryRole.EVIDENCE_AUDITOR
    assert state.accept(report(t, quality=PASSED, paths=("src/a.py", "tests/test_a.py"))) is PipelinePhase.COMMIT
    t=ticket(state)
    assert t.role is DeliveryRole.COMMITTER
    assert state.accept(report(
        t, head=COMMIT, diff="", parent=HEAD,
        committed=("tests/test_a.py", "src/a.py"),
    )) is PipelinePhase.POST_COMMIT_AUDIT
    t=ticket(state)
    assert t.role is DeliveryRole.EVIDENCE_AUDITOR
    assert state.accept(report(t)) is PipelinePhase.LOCAL_AUDITED
    assert state.local_audited
    assert state.expected_role() is None
    assert [h["role"] for h in state.history] == [
        "implementer", "reviewer", "test-engineer", "evidence-auditor",
        "committer", "evidence-auditor",
    ]
    assert state.history[3]["session_id"] != state.history[5]["session_id"]
    assert state.usage["evidence-auditor"] == 2
    assert state.phase is not PipelinePhase.BLOCKED


def test_auditor_is_not_invoked_when_mandatory_prerequisite_red_or_unknown():
    for status in (PrerequisiteStatus.FAIL, PrerequisiteStatus.UNKNOWN):
        state=new()
        to_auditor(state)
        before=dict(state.usage)
        with pytest.raises(PipelineError, match="mandatory quality prerequisite"):
            ticket(state, quality=(PrerequisiteCheck("global", status),))
        assert state.pending is None
        assert state.usage == before
    state=new()
    to_auditor(state)
    with pytest.raises(PipelineError, match="mandatory quality prerequisite"):
        ticket(state)


def test_reviewer_rework_resets_earlier_diff_evidence_and_starts_implementer():
    state=new()
    t=ticket(state)
    state.accept(report(t, diff=UPDATED))
    t=ticket(state)
    assert state.accept(report(t, verdict=PipelineVerdict.REWORK)) is PipelinePhase.IMPLEMENT
    assert state.epoch == 1
    assert state.approved_paths == ()
    assert ticket(state).role is DeliveryRole.IMPLEMENTER


def test_test_engineer_changing_tests_requires_fresh_review():
    state=new()
    t=ticket(state)
    state.accept(report(t, diff=UPDATED))
    t=ticket(state)
    state.accept(report(t))
    t=ticket(state)
    assert state.accept(report(t, diff=DIFF)) is PipelinePhase.REVIEW
    assert state.revision_diff_sha == DIFF
    assert state.epoch == 1
    assert ticket(state).role is DeliveryRole.REVIEWER


def test_readonly_reviewer_report_with_changed_diff_is_rejected():
    state=new()
    t=ticket(state)
    state.accept(report(t, diff=UPDATED))
    t=ticket(state)
    with pytest.raises(PipelineError, match="read-only"):
        state.accept(report(t, diff=DIFF))
    assert state.pending == t
    assert len(state.history) == 1


def test_provenance_wrong_role_or_session_id_fails_closed():
    state=new()
    t=ticket(state)
    bad=report(t)
    object.__setattr__(bad.role_report, "generated_by_role", "reviewer")
    with pytest.raises(PipelineError, match="provenance rejected"):
        state.accept(bad)
    assert state.pending == t
    bad=report(t)
    object.__setattr__(bad.role_report, "agent_session_id", "unrelated")
    with pytest.raises(PipelineError, match="provenance rejected"):
        state.accept(bad)


def test_replay_and_stale_diff_binding_rejected():
    state=new()
    t=ticket(state)
    state.accept(report(t, diff=UPDATED))
    with pytest.raises(PipelineError, match="no runtime-owned role ticket"):
        state.accept(report(t, diff=UPDATED))
    with pytest.raises(PipelineError, match="worktree changed"):
        state.request(run_id="new", session_id="fresh", head_sha=HEAD, diff_sha=DIFF)


def test_committer_requires_exact_parent_and_audited_path_set():
    state=new()
    to_auditor(state)
    t=ticket(state, quality=PASSED)
    state.accept(report(t, quality=PASSED, paths=("src/a.py",)))
    t=ticket(state)
    with pytest.raises(PipelineError, match="commit parent"):
        state.accept(report(t, head=COMMIT, diff="", parent="0"*40, committed=("src/a.py",)))
    with pytest.raises(PipelineError, match="committed paths"):
        state.accept(report(t, head=COMMIT, diff="", parent=HEAD, committed=("src/unapproved.py",)))
    assert state.phase is PipelinePhase.COMMIT
    assert state.pending is t


def test_auditor_requires_unique_safe_approved_paths_and_proven_green_quality():
    state=new()
    to_auditor(state)
    t=ticket(state, quality=PASSED)
    with pytest.raises(PipelineError, match="prerequisites"):
        state.accept(report(t, quality=(PrerequisiteCheck("pytest", PrerequisiteStatus.UNKNOWN),)))
    with pytest.raises(PipelineError, match="unique"):
        state.accept(report(t, quality=PASSED, paths=("a.py", "a.py")))
    with pytest.raises(PipelineError, match="unsafe approved path"):
        state.accept(report(t, quality=PASSED, paths=("../other-repo/private",)))
    assert state.pending == t


def test_blocked_state_is_not_failed_implementation():
    state=new()
    t=ticket(state)
    assert state.accept(report(t, verdict=PipelineVerdict.BLOCKED_EXTERNAL)) is PipelinePhase.BLOCKED
    assert state.blocked_outcome == "BLOCKED_EXTERNAL"
    with pytest.raises(PipelineError, match="not ready"):
        ticket(state)
    failure=new()
    t=ticket(failure)
    assert failure.accept(report(t, verdict=PipelineVerdict.FAILED_IMPLEMENTATION)) is PipelinePhase.FAILED
