"""Read-only, repository-scoped GitHub Issue delivery preflight.

Stage-1 adapter over the existing typed identity and Issue packet primitives.
This is deliberately NOT an implementation/merge executor. No Git or GitHub
mutations and no Copilot sessions are launched by this module.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core.identity import (
    IssueBranch,
    IssueIdentity,
    IssueWorktree,
    RepositoryIdentity,
    WorktreeOwnership,
)
from .core.packet import IssueComment, IssueSnapshot, materialize_issue_packet

_SHA = re.compile(r"^[0-9a-f]{40}$")


class IssuePreflightError(RuntimeError):
    """An Issue delivery invariant cannot be established; fail closed."""


@dataclass(frozen=True, slots=True)
class IssuePreflightResult:
    repository: str
    issue_number: int
    issue_title: str
    worktree: Path
    branch: str
    head_sha: str
    main_sha: str
    pr_number: int
    comment_count: int
    latest_comment_id: int | None
    packet: str


def _run(cwd: Path, *args: str) -> str:
    """Run a bounded, read-only git/gh query, rejecting failed or missing tools."""
    try:
        completed = subprocess.run(
            list(args),
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IssuePreflightError(f"unable to run {args[0]} query: {exc}") from exc
    if completed.returncode != 0:
        reason = (completed.stderr or "").strip()[:500]
        raise IssuePreflightError(
            f"{args[0]} query failed (exit {completed.returncode}): {reason}"
        )
    return completed.stdout.strip()


def _json_output(cwd: Path, *args: str) -> Any:
    try:
        return json.loads(_run(cwd, *args))
    except json.JSONDecodeError as exc:
        raise IssuePreflightError(f"invalid JSON returned by {args[0]}") from exc


def _sha(value: str, *, field: str) -> str:
    if not _SHA.fullmatch(value):
        raise IssuePreflightError(f"{field} must be a full lowercase 40-character SHA")
    return value


def _remote_head(root: Path, branch: str) -> str:
    output = _run(root, "git", "ls-remote", "--heads", "origin", branch)
    matches = [
        row.split()[0]
        for row in output.splitlines()
        if row.endswith(f"refs/heads/{branch}")
    ]
    if len(matches) != 1:
        raise IssuePreflightError(f"cannot verify unique remote branch: {branch}")
    return _sha(matches[0], field=f"origin/{branch}")


def _comments(root: Path, slug: str, number: int) -> tuple[IssueComment, ...]:
    # gh api --paginate returns one JSON array per page. Decode all pages,
    # including repositories with more than 100 comments.
    raw = _run(
        root, "gh", "api", "--paginate",
        f"repos/{slug}/issues/{number}/comments?per_page=100",
    )
    decoder = json.JSONDecoder()
    entries: list[dict[str, Any]] = []
    index = 0
    try:
        while index < len(raw):
            while index < len(raw) and raw[index].isspace():
                index += 1
            if index == len(raw):
                break
            page, index = decoder.raw_decode(raw, index)
            if not isinstance(page, list):
                raise ValueError("expected a comments page array")
            entries.extend(page)
        return tuple(
            IssueComment(
                id=int(row["id"]),
                author=str((row.get("user") or {}).get("login") or "unknown"),
                body=str(row.get("body") or ""),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
                url=str(row["html_url"]),
            )
            for row in entries
        )
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise IssuePreflightError("cannot validate GitHub Issue comments") from exc


def inspect_issue(
    root: Path,
    issue_number: int,
    *,
    expected_head: str | None = None,
    expected_repo: str | None = None,
) -> IssuePreflightResult:
    """Verify existing Issue/worktree/PR identity without making any changes."""
    if issue_number < 1:
        raise IssuePreflightError("issue number must be positive")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise IssuePreflightError("repository root is not a directory")
    if _run(root, "git", "rev-parse", "--show-toplevel") != str(root):
        raise IssuePreflightError("--root must be the main repository checkout")
    if _run(root, "git", "branch", "--show-current") != "main":
        raise IssuePreflightError("root checkout must be on main")
    if _run(root, "git", "status", "--porcelain"):
        raise IssuePreflightError("main checkout is dirty")

    remote_url = _run(root, "git", "remote", "get-url", "origin")
    repository = RepositoryIdentity.from_remote(root=root, remote_url=remote_url)
    slug = repository.slug
    if not slug or (expected_repo is not None and slug != expected_repo):
        raise IssuePreflightError("repository origin identity is missing or mismatched")

    main_sha = _sha(
        _run(root, "git", "rev-parse", "refs/heads/main"), field="local main"
    )
    if main_sha != _remote_head(root, "main"):
        raise IssuePreflightError("local main does not match origin/main")

    worktree = root / ".worktree" / f"issue-{issue_number}"
    if not worktree.is_dir():
        raise IssuePreflightError(f"existing Issue worktree not found: {worktree}")
    worktree = worktree.resolve()
    if _run(worktree, "git", "rev-parse", "--show-toplevel") != str(worktree):
        raise IssuePreflightError("Issue worktree path is not its Git root")
    branch = _run(worktree, "git", "branch", "--show-current")
    identity = IssueIdentity(repository=repository, number=issue_number)
    owned = IssueWorktree(issue=identity, path=worktree, branch=IssueBranch(branch))
    if owned.classify_ownership() is not WorktreeOwnership.OWNED:
        raise IssuePreflightError("Issue worktree/branch ownership mismatch")
    registered = _run(root, "git", "worktree", "list", "--porcelain")
    if f"worktree {worktree}\n" not in registered + "\n" or (
        f"branch refs/heads/{branch}" not in registered
    ):
        raise IssuePreflightError("Issue worktree is not registered on expected branch")
    if _run(worktree, "git", "status", "--porcelain"):
        raise IssuePreflightError("Issue worktree has uncommitted changes")
    head_sha = _sha(_run(worktree, "git", "rev-parse", "HEAD"), field="Issue HEAD")
    if expected_head is not None and head_sha != _sha(expected_head, field="expected head"):
        raise IssuePreflightError("Issue HEAD differs from expected head")
    if head_sha != _remote_head(root, branch):
        raise IssuePreflightError("Issue worktree head differs from remote PR branch")
    _run(worktree, "git", "merge-base", "--is-ancestor", main_sha, head_sha)

    issue = _json_output(
        root, "gh", "issue", "view", str(issue_number),
        "--repo", slug, "--json", "number,title,body,url,state",
    )
    if int(issue["number"]) != issue_number or issue["state"] != "OPEN":
        raise IssuePreflightError("Issue number or lifecycle state mismatch")

    prs = _json_output(
        root, "gh", "pr", "list", "--repo", slug, "--state", "open",
        "--head", branch,
        "--json", "number,headRefName,headRefOid,baseRefName",
    )
    if not isinstance(prs, list) or len(prs) != 1:
        raise IssuePreflightError("expected exactly one existing open PR for Issue branch")
    pr = prs[0]
    if (
        pr["headRefName"] != branch
        or pr["headRefOid"] != head_sha
        or pr["baseRefName"] != "main"
    ):
        raise IssuePreflightError("PR branch, exact head or base mismatch")

    comments = _comments(root, slug, issue_number)
    packet = materialize_issue_packet(
        IssueSnapshot(
            number=issue_number,
            url=str(issue["url"]),
            title=str(issue["title"]),
            body=str(issue.get("body") or ""),
            comments=comments,
        )
    )
    return IssuePreflightResult(
        repository=slug,
        issue_number=issue_number,
        issue_title=str(issue["title"]),
        worktree=worktree,
        branch=branch,
        head_sha=head_sha,
        main_sha=main_sha,
        pr_number=int(pr["number"]),
        comment_count=packet.comments_count,
        latest_comment_id=packet.latest_comment_id,
        packet=packet.markdown,
    )
