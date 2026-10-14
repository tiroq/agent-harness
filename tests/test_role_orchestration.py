from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from task_harness.core.delivery_pipeline import PipelineError, PipelinePhase, PipelineState, PipelineVerdict
from task_harness.core.models import PrerequisiteCheck, PrerequisiteStatus, RoleReport
from task_harness.core.role_orchestration import (
    GitObservation, LocalGitEvidence, RoleReply, drive_pipeline, run_one_role,
)

BASE = "a" * 40
COMMIT = "b" * 40
DIFF = "c" * 64
MODIFIED = "d" * 64
GREEN = (PrerequisiteCheck("global-tests", PrerequisiteStatus.PASS),)


class GitFixture:
    def __init__(self):
        self.current = GitObservation(BASE, DIFF, ("src/a.py",))
    def observe(self):
        return self.current


@dataclass
class RoleFixture:
    git: GitFixture
    invoked: int = 0
    next: GitObservation | None = None
    verdict: PipelineVerdict = PipelineVerdict.PASS
    paths: tuple[str, ...] = ()
    def invoke(self, ticket):
        self.invoked += 1
        if self.next is not None:
            self.git.current = self.next
        return RoleReply(
            report=RoleReport(ticket.role.value, ticket.role.value, ticket.session_id),
            verdict=self.verdict, approved_paths=self.paths,
        )


def test_orchestration_collects_actual_git_not_model_claims():
    git = GitFixture()
    role = RoleFixture(git)
    state = PipelineState.start(
        repository="tiroq/agent-harness", issue=1, pr=2,
        head_sha=BASE, diff_sha=DIFF,
    )
    role.next = GitObservation(BASE, MODIFIED, ("src/a.py",))
    assert run_one_role(state, transport=role, git=git, run_id="implement") is PipelinePhase.REVIEW
    role.next = None
    assert run_one_role(state, transport=role, git=git, run_id="review") is PipelinePhase.INDEPENDENT_TEST
    assert run_one_role(state, transport=role, git=git, run_id="test") is PipelinePhase.PRE_COMMIT_AUDIT
    role.paths = ("src/a.py",)
    assert run_one_role(
        state, transport=role, git=git, run_id="audit", quality=GREEN,
    ) is PipelinePhase.COMMIT
    role.next = GitObservation(COMMIT, "", (), BASE, ("src/a.py",))
    assert run_one_role(state, transport=role, git=git, run_id="commit") is PipelinePhase.POST_COMMIT_AUDIT
    role.next = None
    assert run_one_role(state, transport=role, git=git, run_id="postaudit") is PipelinePhase.LOCAL_AUDITED
    assert role.invoked == 6
    assert state.local_audited


def test_no_premium_auditor_call_when_quality_unknown():
    git = GitFixture()
    role = RoleFixture(git)
    state = PipelineState.start(
        repository="tiroq/agent-harness", issue=1, pr=2,
        head_sha=BASE, diff_sha=DIFF,
    )
    run_one_role(state, transport=role, git=git, run_id="implement")
    run_one_role(state, transport=role, git=git, run_id="review")
    run_one_role(state, transport=role, git=git, run_id="test")
    assert role.invoked == 3
    with pytest.raises(PipelineError, match="mandatory quality"):
        run_one_role(
            state, transport=role, git=git, run_id="audit",
            quality=(PrerequisiteCheck("global", PrerequisiteStatus.UNKNOWN),),
        )
    assert role.invoked == 3


def test_observed_diff_mismatch_blocks_auditor_before_accept():
    git=GitFixture()
    role=RoleFixture(git)
    state=PipelineState.start(repository="tiroq/agent-harness", issue=1, pr=2,
                              head_sha=BASE, diff_sha=DIFF)
    for run in ("implement", "review", "test"):
        run_one_role(state, transport=role, git=git, run_id=run)
    role.paths=("docs/other.md",)
    with pytest.raises(PipelineError, match="auditor paths differ"):
        run_one_role(state, transport=role, git=git, run_id="audit", quality=GREEN)
    assert state.phase is PipelinePhase.PRE_COMMIT_AUDIT


def _git(repo: Path, *cmd: str) -> str:
    result = subprocess.run(
        ["git", *cmd], cwd=repo,
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def test_real_hermetic_git_observation_tracks_untracked_diff_and_commit(tmp_path: Path):
    root=tmp_path/"repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "tests@example.invalid")
    _git(root, "config", "user.name", "Test")
    (root/"src").mkdir()
    (root/"src"/"a.py").write_text("a = 1\n")
    _git(root, "add", "src/a.py")
    _git(root, "commit", "-qm", "initial")
    first=LocalGitEvidence(root).observe()
    assert first.diff_sha==""
    assert first.changed_paths==()
    assert first.parent_sha is None
    (root/"src"/"a.py").write_text("a = 2\n")
    (root/"tests").mkdir()
    (root/"tests"/"test_a.py").write_text("def test_a(): assert True\n")
    changed=LocalGitEvidence(root).observe()
    assert changed.diff_sha
    assert changed.changed_paths == ("src/a.py", "tests/test_a.py")
    assert changed.head_sha==first.head_sha
    _git(root, "add", "src/a.py", "tests/test_a.py")
    _git(root, "commit", "-qm", "add tests")
    after=LocalGitEvidence(root).observe()
    assert after.diff_sha==""
    assert after.changed_paths==()
    assert after.parent_sha==first.head_sha
    assert after.commit_paths==("src/a.py", "tests/test_a.py")


def test_automatic_independent_role_driver_stops_at_local_audited():
    git=GitFixture()
    state=PipelineState.start(repository="tiroq/agent-harness",issue=1,pr=2,
                              head_sha=BASE,diff_sha=DIFF)
    class AutomaticTransport:
        roles=[]
        def invoke(self,ticket):
            self.roles.append((ticket.role.value,ticket.session_id))
            if ticket.phase is PipelinePhase.COMMIT:
                git.current=GitObservation(COMMIT,"",(),BASE,("src/a.py",))
            return RoleReply(
                RoleReport(ticket.role.value,ticket.role.value,ticket.session_id),
                PipelineVerdict.PASS,
                approved_paths=("src/a.py",) if ticket.phase is PipelinePhase.PRE_COMMIT_AUDIT else (),
            )
    class Quality:
        calls=0
        def checks(self):
            self.calls+=1
            return GREEN
    runner=AutomaticTransport()
    quality=Quality()
    assert drive_pipeline(state,transport=runner,git=git,quality=quality,max_roles=6) is PipelinePhase.LOCAL_AUDITED
    assert len(runner.roles)==6
    assert len({session for _,session in runner.roles})==6
    assert quality.calls==1
    assert state.local_audited


def test_automatic_driver_has_hard_role_invocation_limit():
    git=GitFixture()
    role=RoleFixture(git)
    state=PipelineState.start(repository="tiroq/agent-harness",issue=1,pr=2,
                              head_sha=BASE,diff_sha=DIFF)
    class Quality:
        def checks(self):
            return GREEN
    with pytest.raises(PipelineError,match="limit reached"):
        drive_pipeline(state,transport=role,git=git,quality=Quality(),max_roles=2)
    assert role.invoked==2
    assert state.phase is PipelinePhase.INDEPENDENT_TEST
