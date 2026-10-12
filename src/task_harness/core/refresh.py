from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class RefreshVerdict(str, Enum):
    CURRENT = "CURRENT"
    REBASED = "REBASED"
    REWORK = "REWORK"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class RefreshEvidence:
    verdict: RefreshVerdict
    base_freshness: str | None = None
    rebase_performed: bool = False
    merge_performed: bool = False
    patch_equivalence: str | None = None
    issue_head_sha_before: str | None = None
    issue_head_sha_after: str | None = None
    integration_head_sha: str | None = None
    remote_issue_branch_sha: str | None = None
    worktree_recovered: str | None = None
    conflict_paths: tuple[str, ...] = ()
    raw_fields: dict[str, str] = field(default_factory=dict)


def _parse_key_value_lines(output: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if ": " not in line:
            continue
        key, raw_value = line.split(": ", 1)
        fields[key.strip().upper()] = raw_value.strip()
    return fields


def _is_yes(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().upper() in {"1", "TRUE", "YES", "Y"}


def _parse_conflict_paths(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    stripped = value.strip()
    if not stripped:
        return ()
    if stripped.casefold() in {"none", "(none)"}:
        return ()
    return tuple(part for part in (piece.strip() for piece in stripped.split(",")) if part)


def parse_refresh_evidence(output: str) -> RefreshEvidence:
    """
    Parse `scripts/refresh-issue-branch.sh` structured stdout into typed evidence.
    """

    fields = _parse_key_value_lines(output)
    raw_verdict = fields.get("VERDICT")
    if raw_verdict is None:
        msg = "refresh evidence is missing VERDICT field"
        raise ValueError(msg)

    try:
        verdict = RefreshVerdict(raw_verdict.upper())
    except ValueError as exc:
        msg = f"unknown refresh verdict {raw_verdict!r}"
        raise ValueError(msg) from exc

    return RefreshEvidence(
        verdict=verdict,
        base_freshness=fields.get("BASE_FRESHNESS"),
        rebase_performed=_is_yes(fields.get("REBASE_PERFORMED")),
        merge_performed=_is_yes(fields.get("MERGE_PERFORMED")),
        patch_equivalence=fields.get("PATCH_EQUIVALENCE"),
        issue_head_sha_before=fields.get("ISSUE_HEAD_SHA_BEFORE"),
        issue_head_sha_after=fields.get("ISSUE_HEAD_SHA_AFTER"),
        integration_head_sha=fields.get("INTEGRATION_HEAD_SHA"),
        remote_issue_branch_sha=fields.get("REMOTE_ISSUE_BRANCH_SHA"),
        worktree_recovered=fields.get("WORKTREE_RECOVERED"),
        conflict_paths=_parse_conflict_paths(fields.get("CONFLICT_PATHS")),
        raw_fields=fields,
    )
