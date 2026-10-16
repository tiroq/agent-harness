"""Repository-neutral role transport boundary for deterministic delivery.

Adapters supply a fresh independent role invocation and trusted Git
observations. The model's reported SHA/changed-path claims are never
substituted for locally observed Git facts.
"""

from __future__ import annotations

import hashlib
import secrets
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .delivery_pipeline import (
    PipelineError, PipelinePhase, PipelineState, PipelineVerdict,
    RoleEvidence, RoleTicket,
)
from .models import PrerequisiteCheck, RoleReport


@dataclass(frozen=True, slots=True)
class GitObservation:
    head_sha: str
    diff_sha: str
    changed_paths: tuple[str, ...] = ()
    parent_sha: str | None = None
    commit_paths: tuple[str, ...] = ()
    path_fingerprints: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RoleReply:
    report: RoleReport
    verdict: PipelineVerdict
    # Auditor allowlist is independently reconciled with git changed_paths.
    approved_paths: tuple[str, ...] = ()


class RoleTransport(Protocol):
    def invoke(self, ticket: RoleTicket) -> RoleReply: ...


class GitEvidence(Protocol):
    def observe(self) -> GitObservation: ...


def run_one_role(
    state: PipelineState,
    *,
    transport: RoleTransport,
    git: GitEvidence,
    run_id: str,
    session_id: str | None = None,
    quality: tuple[PrerequisiteCheck, ...] = (),
) -> PipelinePhase:
    """One role per call; caller persists state before/after invoking transport.

    Only the authoritative GitEvidence adapter supplies head/diff/commit path
    facts. No remote publication or merge is possible through this boundary.
    """
    before = git.observe()
    if before.head_sha != state.current_head_sha or before.diff_sha != state.revision_diff_sha:
        raise PipelineError("worktree changed outside accepted workflow state")
    ticket = state.request(
        run_id=run_id, session_id=session_id or secrets.token_hex(16),
        head_sha=before.head_sha, diff_sha=before.diff_sha, quality=quality,
    )
    reply = transport.invoke(ticket)
    after = git.observe()
    return accept_role_checkpoint(
        state, ticket=ticket, reply=reply, before=before, after=after,
        quality=quality,
    )


def accept_role_checkpoint(
    state: PipelineState,
    *,
    ticket: RoleTicket,
    reply: RoleReply,
    before: GitObservation,
    after: GitObservation,
    quality: tuple[PrerequisiteCheck, ...] = (),
) -> PipelinePhase:
    """Validate an existing internal-subagent report without launching an agent.

    The caller must acquire trusted subagent invocation identity separately;
    ROLE/AGENT_SESSION_ID claims in report text alone do not authenticate it.
    """
    if state.pending != ticket:
        raise PipelineError("role ticket no longer owns the pending phase")
    if ticket.phase is PipelinePhase.INDEPENDENT_TEST:
        previous = dict(before.path_fingerprints)
        current = dict(after.path_fingerprints)
        for path in set(previous) | set(current):
            if previous.get(path) != current.get(path) and not (
                path.startswith("tests/") or path.startswith("test/")
                or path.split("/")[-1].startswith("test_")
            ):
                raise PipelineError("test engineer changed non-test source path")
    if ticket.phase is PipelinePhase.PRE_COMMIT_AUDIT:
        if not reply.approved_paths:
            raise PipelineError("auditor did not approve commit paths")
        if set(reply.approved_paths) != set(after.changed_paths):
            raise PipelineError("auditor paths differ from observed dirty Git paths")
    evidence = RoleEvidence(
        role_report=reply.report,
        verdict=reply.verdict,
        run_id=ticket.run_id,
        session_id=ticket.session_id,
        head_before=before.head_sha,
        head_after=after.head_sha,
        diff_before=before.diff_sha,
        diff_after=after.diff_sha,
        approved_paths=reply.approved_paths,
        committed_paths=after.commit_paths if ticket.phase is PipelinePhase.COMMIT else (),
        commit_parent_sha=after.parent_sha if ticket.phase is PipelinePhase.COMMIT else None,
        quality=quality,
    )
    return state.accept(evidence)


class QualityProvider(Protocol):
    def checks(self) -> tuple[PrerequisiteCheck, ...]: ...


def drive_pipeline(
    state: PipelineState,
    *,
    transport: RoleTransport,
    git: GitEvidence,
    quality: QualityProvider,
    max_roles: int = 12,
) -> PipelinePhase:
    """Execute independent roles in order, bounded against repeated REWORK.

    This reusable core has no GitHub writes or automatic merge. Real transports
    must implement process ownership and durable state in their host adapters;
    a fake transport can exercise the whole lifecycle without Copilot charges.
    """
    if max_roles < 1:
        raise PipelineError("role invocation limit must be positive")
    for index in range(max_roles):
        if state.phase in {
            PipelinePhase.LOCAL_AUDITED, PipelinePhase.BLOCKED, PipelinePhase.FAILED
        }:
            return state.phase
        prerequisites = (
            quality.checks() if state.phase is PipelinePhase.PRE_COMMIT_AUDIT else ()
        )
        run_one_role(
            state, transport=transport, git=git,
            run_id=f"role-{index}-{secrets.token_hex(6)}",
            quality=prerequisites,
        )
    if state.phase is not PipelinePhase.LOCAL_AUDITED:
        raise PipelineError("bounded independent-role limit reached; no automatic extra spend")
    return state.phase


class LocalGitEvidence:
    """Bounded local trusted observations; no Git mutation or fetch."""

    def __init__(self, worktree: Path, *, max_bytes: int = 16 * 1024 * 1024):
        self.worktree = worktree.resolve()
        self.max_bytes = max_bytes

    def _git(self, *args: str) -> bytes:
        try:
            output = subprocess.run(
                ["git", *args], cwd=self.worktree,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                check=False, timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PipelineError(f"git evidence command unavailable: {exc}") from exc
        if output.returncode:
            raise PipelineError(
                f"git evidence command failed: {output.stderr.decode(errors='replace')[:200]}"
            )
        if len(output.stdout) > self.max_bytes:
            raise PipelineError("git evidence exceeds configured bounded input")
        return output.stdout

    def observe(self) -> GitObservation:
        head = self._git("rev-parse", "HEAD").decode().strip()
        # Binary diff covers tracked modifications and staged changes.
        raw_diff = self._git("diff", "--binary", "HEAD", "--")
        untracked = self._git("ls-files", "--others", "--exclude-standard", "-z").split(b"\x00")
        digest = hashlib.sha256()
        digest.update(raw_diff)
        # By default, Git collapses untracked trees into directory entries.
        # Expand them to file-level paths for trustworthy evidence and auditing.
        paths_raw = self._git("status", "--porcelain=v1", "-z", "--untracked-files=all")
        paths = set()
        records = paths_raw.split(b"\x00")
        i = 0
        while i < len(records):
            row = records[i]
            i += 1
            if not row:
                continue
            if len(row) < 4:
                raise PipelineError("malformed porcelain Git status")
            name = row[3:].decode("utf-8", errors="surrogateescape")
            paths.add(name)
            if "R" in row[:2].decode(errors="replace") or "C" in row[:2].decode(errors="replace"):
                if i >= len(records) or not records[i]:
                    raise PipelineError("malformed rename in Git status")
                paths.add(records[i].decode("utf-8", errors="surrogateescape"))
                i += 1
        total = len(raw_diff)
        for path_raw in sorted(p for p in untracked if p):
            path = path_raw.decode("utf-8", errors="surrogateescape")
            file = (self.worktree / path)
            if file.is_symlink() or not file.is_file():
                raise PipelineError("untracked non-regular path needs manual review")
            stat = file.stat()
            total += stat.st_size
            if total > self.max_bytes:
                raise PipelineError("untracked evidence exceeds configured bound")
            digest.update(path_raw + b"\x00" + file.read_bytes())
        if not paths and not raw_diff and not any(untracked):
            diff_sha = ""
        else:
            diff_sha = digest.hexdigest()
        # A commit audit must confirm parent via git, never from role prose.
        parents = self._git("rev-list", "--parents", "-n", "1", "HEAD").decode().split()
        parent = parents[1] if len(parents) > 1 else None
        changed_commit = self._git(
            "diff-tree", "--root", "--no-commit-id", "--name-only", "-r", "-z", "HEAD"
        )
        commit_paths = tuple(sorted(
            name.decode("utf-8", errors="surrogateescape")
            for name in changed_commit.split(bytes([0])) if name
        ))
        path_fingerprints = []
        file_bytes = 0
        for name in sorted(paths):
            path = self.worktree / name
            if path.is_symlink():
                raise PipelineError("symlink in worktree evidence requires manual review")
            if path.is_file():
                size = path.stat().st_size
                file_bytes += size
                if file_bytes > self.max_bytes:
                    raise PipelineError("modified file evidence exceeds configured bound")
                path_fingerprints.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
            elif path.exists():
                raise PipelineError("non-regular changed path requires manual review")
            else:
                path_fingerprints.append((name, "DELETED"))
        return GitObservation(
            head_sha=head, diff_sha=diff_sha,
            changed_paths=tuple(sorted(paths)), parent_sha=parent,
            commit_paths=commit_paths, path_fingerprints=tuple(path_fingerprints),
        )
