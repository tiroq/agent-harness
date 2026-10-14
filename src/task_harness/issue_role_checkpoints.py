"""Issue-local checkpoints for Copilot's *internal* delegated subagents.

The only parent process/session is owned by issue_execution. This module
accepts orchestrator-submitted reports; it NEVER starts Copilot or chooses
which subagent to launch. Subagent identity is caller-asserted until a
trusted Copilot tool/event identity adapter is available.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from pathlib import Path
from typing import Any

from .core.delivery_pipeline import (
    PipelineError, PipelinePhase, PipelineState, PipelineVerdict, RoleTicket,
)
from .core.models import RoleReport
from .core.role_orchestration import (
    GitObservation, LocalGitEvidence, RoleReply, accept_role_checkpoint,
)
from .issue_execution import (
    IssueExecutionError, _clock, _guard, _load, _same_process, _save,
)

_MAX_REPORT = 256 * 1024
_SUBAGENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{3,127}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
_KEYS = frozenset({"ROLE", "GENERATED_BY_ROLE", "AGENT_SESSION_ID", "VERDICT", "APPROVED_TASK_PATHS"})


def _snapshot(state: PipelineState) -> dict[str, Any]:
    data = dataclasses.asdict(state)
    data["phase"] = state.phase.value
    data["accepted_sessions"] = sorted(state.accepted_sessions)
    if state.pending is not None:
        data["pending"]["phase"] = state.pending.phase.value
        data["pending"]["role"] = state.pending.role.value
    return data


def _restore(data: dict[str, Any]) -> PipelineState:
    try:
        from .core.models import DeliveryRole
        pending = data.get("pending")
        ticket = None
        if pending is not None:
            ticket = RoleTicket(
                phase=PipelinePhase(pending["phase"]),
                role=DeliveryRole(pending["role"]),
                run_id=pending["run_id"], session_id=pending["session_id"],
                head_sha=pending["head_sha"], diff_sha=pending["diff_sha"],
                epoch=int(pending["epoch"]),
            )
        return PipelineState(
            repository=data["repository"], issue=int(data["issue"]),
            pr=int(data["pr"]), base_head_sha=data["base_head_sha"],
            phase=PipelinePhase(data["phase"]), epoch=int(data["epoch"]),
            revision_diff_sha=data["revision_diff_sha"],
            current_head_sha=data["current_head_sha"],
            approved_paths=tuple(data["approved_paths"]),
            approved_diff_sha=data["approved_diff_sha"], pending=ticket,
            accepted_sessions=set(data["accepted_sessions"]),
            usage=dict(data["usage"]), history=list(data["history"]),
            blocked_outcome=data.get("blocked_outcome"),
        )
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise IssueExecutionError("corrupt role checkpoint state; refusing phase mutation") from exc


def _checkpoint_state(checkpoint: Any) -> PipelineState:
    if not isinstance(checkpoint, dict) or not isinstance(checkpoint.get("pipeline"), dict):
        raise IssueExecutionError("corrupt role checkpoint state; refusing phase mutation")
    return _restore(checkpoint["pipeline"])


def _observation_to_dict(observation: GitObservation) -> dict[str, Any]:
    return dataclasses.asdict(observation)


def _observation_from_dict(data: dict[str, Any]) -> GitObservation:
    try:
        return GitObservation(
            head_sha=data["head_sha"], diff_sha=data["diff_sha"],
            changed_paths=tuple(data["changed_paths"]),
            parent_sha=data.get("parent_sha"),
            commit_paths=tuple(data["commit_paths"]),
            path_fingerprints=tuple(tuple(p) for p in data["path_fingerprints"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise IssueExecutionError("corrupt checkpoint Git evidence") from exc


def _parent(ledger: dict[str, Any] | None, *, issue: int, run_id: str, session_id: str) -> dict[str, Any]:
    if not _RUN_ID.fullmatch(run_id):
        raise IssueExecutionError("invalid parent run ID")
    if (
        ledger is None
        or ledger.get("issue") != issue
        or ledger.get("run_id") != run_id
        or ledger.get("session_id") != session_id
        or ledger.get("status") != "RUNNING"
        or not isinstance(ledger.get("worktree"), str)
        or not isinstance(ledger.get("repo"), str)
        or not isinstance(ledger.get("pr"), int)
        or not isinstance(ledger.get("head"), str)
        or not _same_process(ledger)
    ):
        raise IssueExecutionError("parent run/session is not the live owning Copilot process")
    return ledger


def _git(ledger: dict[str, Any], root: Path, issue: int) -> GitObservation:
    worktree = Path(ledger["worktree"]).resolve()
    expected = root.resolve() / ".worktree" / f"issue-{issue}"
    if worktree != expected or not worktree.is_dir():
        raise IssueExecutionError("Issue worktree ownership changed or is missing")
    return LocalGitEvidence(worktree).observe()


def _read_report(root: Path, run_id: str, report: str) -> tuple[RoleReport, PipelineVerdict, tuple[str, ...], str]:
    if not report or "/" in report or chr(92) in report or report in {".", ".."} or not report.endswith(".md"):
        raise IssueExecutionError("report must be a simple .md filename")
    if not _RUN_ID.fullmatch(run_id):
        raise IssueExecutionError("invalid parent run ID")
    runs_root = (root.resolve() / ".copilot-workflow" / "runs").resolve()
    base = (runs_root / run_id).resolve()
    if base.parent != runs_root:
        raise IssueExecutionError("role report run artifact path escapes repository")
    file = base / report
    if file.is_symlink() or not file.is_file() or file.resolve().parent != base:
        raise IssueExecutionError("report must be a regular file inside the owning run artifacts")
    size = file.stat().st_size
    if size > _MAX_REPORT:
        raise IssueExecutionError("subagent report exceeds bounded size")
    raw = file.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise IssueExecutionError("report must contain valid UTF-8") from exc
    fields: dict[str, str] = {}
    for line in text.splitlines():
        m = re.fullmatch(r"\s*([A-Z_]+):\s*(.*?)\s*", line)
        if m and m.group(1) in _KEYS:
            key = m.group(1)
            if key in fields:
                raise IssueExecutionError(f"duplicate role report field: {key}")
            fields[key] = m.group(2)
    required = {"ROLE", "GENERATED_BY_ROLE", "AGENT_SESSION_ID", "VERDICT"}
    if not required <= fields.keys() or any(not fields[k] for k in required):
        raise IssueExecutionError("report missing role provenance or verdict")
    try:
        verdict = PipelineVerdict(fields["VERDICT"].upper())
    except ValueError as exc:
        raise IssueExecutionError("unsupported role verdict") from exc
    paths = tuple(p.strip() for p in fields.get("APPROVED_TASK_PATHS", "").split(",") if p.strip())
    role = RoleReport(
        role=fields["ROLE"], generated_by_role=fields["GENERATED_BY_ROLE"],
        agent_session_id=fields["AGENT_SESSION_ID"],
        content=text,
    )
    return role, verdict, paths, hashlib.sha256(raw).hexdigest()


def role_begin(root: Path, issue: int, *, run_id: str, parent_session_id: str, subagent_id: str) -> dict[str, Any]:
    """Checkpoint BEFORE reading subagent's result. Never invokes Copilot."""
    if not _SUBAGENT_ID.fullmatch(subagent_id) or subagent_id == parent_session_id:
        raise IssueExecutionError("invalid or parent-reused subagent identity")
    with _guard(root, issue) as path:
        ledger = _parent(_load(path), issue=issue, run_id=run_id, session_id=parent_session_id)
        role_data = ledger.get("role_checkpoint")
        observed = _git(ledger, root, issue)
        if role_data is None:
            if observed.head_sha != ledger["head"]:
                raise IssueExecutionError("parent HEAD changed before role checkpoint initialization")
            state = PipelineState.start(
                repository=ledger["repo"], issue=issue, pr=ledger["pr"],
                head_sha=observed.head_sha, diff_sha=observed.diff_sha,
            )
        else:
            state = _checkpoint_state(role_data)
            if (state.repository != ledger["repo"] or state.issue != issue
                    or state.pr != ledger["pr"]):
                raise IssueExecutionError("role checkpoint identity mismatch")
            if state.pending is not None or role_data.get("before") is not None:
                raise IssueExecutionError("unfinished subagent checkpoint; cannot issue second ticket")
        try:
            ticket = state.request(
                run_id=run_id, session_id=subagent_id,
                head_sha=observed.head_sha, diff_sha=observed.diff_sha,
            )
        except PipelineError as exc:
            raise IssueExecutionError(f"role checkpoint blocked: {exc}") from exc
        ledger["role_checkpoint"] = {
            "pipeline": _snapshot(state),
            "before": _observation_to_dict(observed),
            "identity_source": "ORCHESTRATOR_ASSERTED_UNVERIFIED",
            "last_change": _clock(),
        }
        _save(path, ledger)
        return {
            "run_id": run_id, "parent_session_id": parent_session_id,
            "role": ticket.role.value, "phase": ticket.phase.value,
            "subagent_id": ticket.session_id,
            "identity_source": "ORCHESTRATOR_ASSERTED_UNVERIFIED",
            "status": "ROLE_PENDING",
        }


def role_complete(
    root: Path, issue: int, *, run_id: str, parent_session_id: str, report: str,
) -> dict[str, Any]:
    """Ingest a saved internal-subagent report + trusted current Git evidence."""
    with _guard(root, issue) as path:
        ledger = _parent(_load(path), issue=issue, run_id=run_id, session_id=parent_session_id)
        role_data = ledger.get("role_checkpoint")
        if not isinstance(role_data, dict) or role_data.get("before") is None:
            raise IssueExecutionError("no pending internal-subagent checkpoint")
        state = _checkpoint_state(role_data)
        ticket = state.pending
        if ticket is None or ticket.run_id != run_id:
            raise IssueExecutionError("role ticket does not belong to parent run")
        before = _observation_from_dict(role_data["before"])
        role_report, verdict, paths, report_sha = _read_report(root, run_id, report)
        observed = _git(ledger, root, issue)
        try:
            phase = accept_role_checkpoint(
                state, ticket=ticket, reply=RoleReply(
                    report=role_report, verdict=verdict, approved_paths=paths,
                ),
                before=before, after=observed,
            )
        except PipelineError as exc:
            raise IssueExecutionError(f"subagent checkpoint rejected: {exc}") from exc
        ledger["role_checkpoint"] = {
            "pipeline": _snapshot(state), "before": None,
            "identity_source": "ORCHESTRATOR_ASSERTED_UNVERIFIED",
            "last_report_sha256": report_sha, "last_change": _clock(),
        }
        _save(path, ledger)
        return {
            "run_id": run_id, "completed_role": ticket.role.value,
            "subagent_id": ticket.session_id,
            "phase": phase.value, "report_sha256": report_sha,
            "identity_source": "ORCHESTRATOR_ASSERTED_UNVERIFIED",
            "pr_ready": False,
        }


def role_status(root: Path, issue: int) -> dict[str, Any]:
    """Read-only view; never promotes a self-reported role to GitHub-ready."""
    path = root.resolve() / ".copilot-workflow" / "issue-runs" / f"issue-{issue}.json"
    ledger = _load(path)
    if ledger is None:
        return {"issue": issue, "status": "NOT_STARTED", "pr_ready": False}
    info = ledger.get("role_checkpoint")
    if info is None:
        return {"issue": issue, "status": "NO_ROLE_EVIDENCE", "pr_ready": False}
    state = _checkpoint_state(info)
    return {
        "issue": issue, "parent_run_id": ledger["run_id"],
        "parent_session_id": ledger["session_id"],
        "status": "ROLE_PENDING" if state.pending else "CHECKPOINTED",
        "phase": state.phase.value,
        "pending_role": state.expected_role().value if state.pending and state.expected_role() else None,
        "accepted_roles": len(state.history),
        "role_invocations": state.usage,
        "identity_source": "ORCHESTRATOR_ASSERTED_UNVERIFIED",
        "pr_ready": False,
    }
