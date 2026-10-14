"""Typed, deterministic independent-role delivery gates.

This is the reusable lifecycle core used by CLI/MCP adapters. A role runner
must supply an independently launched role session; model text is never
trusted as authorization for a commit or remote publication.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from .gates import decide_pre_audit_gate
from .intake import intake_role_report
from .models import (
    DeliveryOutcome, DeliveryRole, PrerequisiteCheck, PrerequisiteStatus, RoleReport,
)


class PipelinePhase(str, Enum):
    IMPLEMENT = "IMPLEMENT"
    REVIEW = "REVIEW"
    INDEPENDENT_TEST = "INDEPENDENT_TEST"
    PRE_COMMIT_AUDIT = "PRE_COMMIT_AUDIT"
    COMMIT = "COMMIT"
    POST_COMMIT_AUDIT = "POST_COMMIT_AUDIT"
    LOCAL_AUDITED = "LOCAL_AUDITED"
    REWORK = "REWORK"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class PipelineVerdict(str, Enum):
    PASS = "PASS"
    REWORK = "REWORK"
    BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"
    BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"
    BLOCKED_AUTHORITY = "BLOCKED_AUTHORITY"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    FAILED_IMPLEMENTATION = "FAILED_IMPLEMENTATION"


_PHASE_ROLE: dict[PipelinePhase, DeliveryRole] = {
    PipelinePhase.IMPLEMENT: DeliveryRole.IMPLEMENTER,
    PipelinePhase.REVIEW: DeliveryRole.REVIEWER,
    PipelinePhase.INDEPENDENT_TEST: DeliveryRole.TEST_ENGINEER,
    PipelinePhase.PRE_COMMIT_AUDIT: DeliveryRole.EVIDENCE_AUDITOR,
    PipelinePhase.COMMIT: DeliveryRole.COMMITTER,
    PipelinePhase.POST_COMMIT_AUDIT: DeliveryRole.EVIDENCE_AUDITOR,
}
_READ_ONLY = frozenset(
    {PipelinePhase.REVIEW, PipelinePhase.PRE_COMMIT_AUDIT, PipelinePhase.POST_COMMIT_AUDIT}
)


class PipelineError(ValueError):
    """A deterministic phase/provenance/quality prerequisite is not proven."""


@dataclass(frozen=True, slots=True)
class RoleTicket:
    phase: PipelinePhase
    role: DeliveryRole
    run_id: str
    session_id: str
    head_sha: str
    diff_sha: str
    epoch: int


@dataclass(frozen=True, slots=True)
class RoleEvidence:
    role_report: RoleReport
    verdict: PipelineVerdict
    run_id: str
    session_id: str
    head_before: str
    head_after: str
    diff_before: str
    diff_after: str
    # Approved paths are supplied by independent pre-commit evidence auditor,
    # not trusted blindly from a commit role.
    approved_paths: tuple[str, ...] = ()
    committed_paths: tuple[str, ...] = ()
    commit_parent_sha: str | None = None
    quality: tuple[PrerequisiteCheck, ...] = ()


@dataclass(slots=True)
class PipelineState:
    repository: str
    issue: int
    pr: int
    base_head_sha: str
    phase: PipelinePhase = PipelinePhase.IMPLEMENT
    epoch: int = 0
    revision_diff_sha: str = ""
    current_head_sha: str = ""
    approved_paths: tuple[str, ...] = ()
    approved_diff_sha: str = ""
    pending: RoleTicket | None = None
    accepted_sessions: set[str] = field(default_factory=set)
    usage: dict[str, int] = field(default_factory=dict)
    history: list[dict[str, str]] = field(default_factory=list)
    blocked_outcome: str | None = None

    @classmethod
    def start(cls, *, repository: str, issue: int, pr: int, head_sha: str, diff_sha: str) -> "PipelineState":
        if not repository or issue < 1 or pr < 1 or not head_sha:
            raise PipelineError("pipeline requires bound repository, Issue, PR and HEAD")
        return cls(repository=repository, issue=issue, pr=pr, base_head_sha=head_sha,
                   current_head_sha=head_sha, revision_diff_sha=diff_sha)

    def expected_role(self) -> DeliveryRole | None:
        return _PHASE_ROLE.get(self.phase)

    def request(
        self, *, run_id: str, session_id: str, head_sha: str,
        diff_sha: str, quality: tuple[PrerequisiteCheck, ...] = (),
    ) -> RoleTicket:
        role = self.expected_role()
        if role is None or self.pending is not None:
            raise PipelineError("pipeline phase not ready for new independent role")
        if not run_id or not session_id or session_id in self.accepted_sessions:
            raise PipelineError("role run ID and unique session ID are required")
        if head_sha != self.current_head_sha or diff_sha != self.revision_diff_sha:
            raise PipelineError("worktree changed since accepted delivery revision")
        if self.phase is PipelinePhase.PRE_COMMIT_AUDIT:
            # Do not spend a premium auditor call when a mandatory gate
            # is already FAIL or UNKNOWN; require explicit fresh evidence.
            if not quality or any(
                check.mandatory and check.status is not PrerequisiteStatus.PASS
                for check in quality
            ):
                raise PipelineError("mandatory quality prerequisite red/unknown before auditor")
            if not decide_pre_audit_gate(quality).should_invoke_auditor:
                raise PipelineError("known-red quality prerequisite before auditor")
        ticket = RoleTicket(
            phase=self.phase, role=role, run_id=run_id, session_id=session_id,
            head_sha=head_sha, diff_sha=diff_sha, epoch=self.epoch,
        )
        self.pending = ticket
        self.usage[role.value] = self.usage.get(role.value, 0) + 1
        return ticket

    def accept(self, evidence: RoleEvidence) -> PipelinePhase:
        ticket = self.pending
        if ticket is None:
            raise PipelineError("no runtime-owned role ticket; unsolicited report")
        if (
            evidence.run_id != ticket.run_id
            or evidence.session_id != ticket.session_id
            or ticket.epoch != self.epoch
            or evidence.head_before != ticket.head_sha
            or evidence.diff_before != ticket.diff_sha
        ):
            raise PipelineError("stale or mismatched role execution identity/diff/HEAD")
        result = intake_role_report(ticket.role, ticket.session_id, evidence.role_report)
        if not result.accepted:
            raise PipelineError("role provenance rejected: " + "; ".join(result.validation.errors))
        if ticket.session_id in self.accepted_sessions:
            raise PipelineError("role report session replay")
        if ticket.phase in _READ_ONLY and (
            evidence.diff_after != evidence.diff_before or evidence.head_after != evidence.head_before
        ):
            raise PipelineError("read-only independent role modified Git state")

        if evidence.verdict is PipelineVerdict.REWORK:
            if ticket.phase not in {PipelinePhase.REVIEW, PipelinePhase.INDEPENDENT_TEST,
                                    PipelinePhase.PRE_COMMIT_AUDIT, PipelinePhase.POST_COMMIT_AUDIT}:
                raise PipelineError("REWORK is not valid from this phase")
            self._record(ticket, evidence)
            self.epoch += 1
            self.phase = PipelinePhase.IMPLEMENT
            self.revision_diff_sha = evidence.diff_after
            self.current_head_sha = evidence.head_after
            self.approved_paths = ()
            self.approved_diff_sha = ""
            return self.phase
        if evidence.verdict is not PipelineVerdict.PASS:
            self._record(ticket, evidence)
            self.blocked_outcome = evidence.verdict.value
            self.phase = (
                PipelinePhase.FAILED if evidence.verdict is PipelineVerdict.FAILED_IMPLEMENTATION
                else PipelinePhase.BLOCKED
            )
            return self.phase

        if ticket.phase is PipelinePhase.IMPLEMENT:
            if evidence.head_after != ticket.head_sha:
                raise PipelineError("implementer must not commit before review")
            self.revision_diff_sha = evidence.diff_after
            self.phase = PipelinePhase.REVIEW

        elif ticket.phase is PipelinePhase.REVIEW:
            self.phase = PipelinePhase.INDEPENDENT_TEST

        elif ticket.phase is PipelinePhase.INDEPENDENT_TEST:
            if evidence.head_after != ticket.head_sha:
                raise PipelineError("test engineer must not commit")
            if evidence.diff_after != ticket.diff_sha:
                # New test-only diff invalidates the earlier review. Review
                # again; report data alone cannot attest production/test scope.
                self.epoch += 1
                self.revision_diff_sha = evidence.diff_after
                self.phase = PipelinePhase.REVIEW
            else:
                self.phase = PipelinePhase.PRE_COMMIT_AUDIT

        elif ticket.phase is PipelinePhase.PRE_COMMIT_AUDIT:
            mandatory = evidence.quality
            if not mandatory or any(
                q.mandatory and q.status is not PrerequisiteStatus.PASS for q in mandatory
            ):
                raise PipelineError("mandatory global/quality prerequisites unknown or red")
            decision = decide_pre_audit_gate(mandatory)
            if not decision.should_invoke_auditor:
                raise PipelineError(decision.reason)
            if not evidence.approved_paths or len(set(evidence.approved_paths)) != len(evidence.approved_paths):
                raise PipelineError("auditor must explicitly approve unique commit paths")
            if any(not _safe_repo_path(p) for p in evidence.approved_paths):
                raise PipelineError("unsafe approved path")
            self.approved_paths = evidence.approved_paths
            self.approved_diff_sha = evidence.diff_after
            self.phase = PipelinePhase.COMMIT

        elif ticket.phase is PipelinePhase.COMMIT:
            if ticket.diff_sha != self.approved_diff_sha:
                raise PipelineError("approved diff changed before commit")
            if evidence.head_after == evidence.head_before or evidence.commit_parent_sha != ticket.head_sha:
                raise PipelineError("commit parent or new HEAD not verified")
            if evidence.diff_after:
                raise PipelineError("post-commit worktree must be clean")
            if (not evidence.committed_paths or
                set(evidence.committed_paths) != set(self.approved_paths) or
                len(set(evidence.committed_paths)) != len(evidence.committed_paths)):
                raise PipelineError("committed paths do not match audited allowlist")
            self.current_head_sha = evidence.head_after
            self.revision_diff_sha = ""
            self.phase = PipelinePhase.POST_COMMIT_AUDIT

        elif ticket.phase is PipelinePhase.POST_COMMIT_AUDIT:
            if evidence.diff_after or evidence.head_after != self.current_head_sha:
                raise PipelineError("post-commit independent audit requires clean exact commit")
            self.phase = PipelinePhase.LOCAL_AUDITED

        else:
            raise PipelineError("unsupported role completion")

        self._record(ticket, evidence)
        return self.phase

    def _record(self, ticket: RoleTicket, evidence: RoleEvidence) -> None:
        self.history.append({
            "epoch": str(ticket.epoch),
            "phase": ticket.phase.value,
            "role": ticket.role.value,
            "run_id": ticket.run_id,
            "session_id": ticket.session_id,
            "verdict": evidence.verdict.value,
            "head_before": evidence.head_before,
            "head_after": evidence.head_after,
            "diff_before": evidence.diff_before,
            "diff_after": evidence.diff_after,
        })
        self.accepted_sessions.add(ticket.session_id)
        self.pending = None

    @property
    def local_audited(self) -> bool:
        # This is deliberately NOT GitHub PR_READY.
        return self.phase is PipelinePhase.LOCAL_AUDITED


def _safe_repo_path(path: str) -> bool:
    from pathlib import PurePosixPath
    if not path or path.startswith("/") or "\\\\" in path:
        return False
    parts = PurePosixPath(path).parts
    return bool(parts) and all(p not in {"..", "."} for p in parts)
