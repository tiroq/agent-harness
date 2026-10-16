from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_GITHUB_SSH_PATTERN = re.compile(
    r"^git@github\.com:(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?$"
)
_GITHUB_HTTPS_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<name>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)
_WORKTREE_ISSUE_PATTERN = re.compile(r"^issue-(?P<number>\d+)$")
_BRANCH_ISSUE_PATTERNS = (
    re.compile(r"^(?P<number>\d+)(?:[-_].*)?$"),
    re.compile(r"^issue[-_](?P<number>\d+)(?:[-_].*)?$", flags=re.IGNORECASE),
)


def parse_repository_slug(remote_url: str) -> tuple[str, str] | None:
    """Parse owner/name from a GitHub SSH or HTTPS remote URL."""

    for pattern in (_GITHUB_SSH_PATTERN, _GITHUB_HTTPS_PATTERN):
        match = pattern.match(remote_url.strip())
        if not match:
            continue
        owner = match.group("owner")
        name = match.group("name")
        if owner and name:
            return owner, name
    return None


def issue_number_from_branch_name(branch_name: str) -> int | None:
    """Extract issue number from common issue branch names."""

    segment = branch_name.strip().rsplit("/", 1)[-1]
    if not segment:
        return None
    for pattern in _BRANCH_ISSUE_PATTERNS:
        match = pattern.match(segment)
        if match:
            return int(match.group("number"))
    return None


def issue_number_from_worktree_path(worktree_path: Path) -> int | None:
    """Extract issue number from any `issue-<n>` path segment."""

    for part in reversed(worktree_path.parts):
        match = _WORKTREE_ISSUE_PATTERN.match(part)
        if match:
            return int(match.group("number"))
    return None


class WorktreeOwnership(str, Enum):
    OWNED = "owned"
    FOREIGN_ISSUE = "foreign_issue"
    FOREIGN_BRANCH = "foreign_branch"
    OUTSIDE_REPOSITORY = "outside_repository"


@dataclass(frozen=True, slots=True)
class RepositoryIdentity:
    root: Path
    owner: str | None = None
    name: str | None = None

    @property
    def slug(self) -> str | None:
        if self.owner is None or self.name is None:
            return None
        return f"{self.owner}/{self.name}"

    @classmethod
    def from_remote(cls, *, root: Path, remote_url: str) -> RepositoryIdentity:
        slug = parse_repository_slug(remote_url)
        if slug is None:
            return cls(root=root.resolve())
        owner, name = slug
        return cls(root=root.resolve(), owner=owner, name=name)


@dataclass(frozen=True, slots=True)
class IssueIdentity:
    repository: RepositoryIdentity
    number: int

    @property
    def ref(self) -> str:
        slug = self.repository.slug
        return f"{slug}#{self.number}" if slug else f"#{self.number}"


@dataclass(frozen=True, slots=True)
class IssueBranch:
    name: str

    @property
    def issue_number(self) -> int | None:
        return issue_number_from_branch_name(self.name)


@dataclass(frozen=True, slots=True)
class IssueWorktree:
    issue: IssueIdentity
    path: Path
    branch: IssueBranch

    def classify_ownership(self) -> WorktreeOwnership:
        return classify_worktree_ownership(
            repo_root=self.issue.repository.root,
            worktree_path=self.path,
            expected_issue_number=self.issue.number,
            branch_name=self.branch.name,
        )


def classify_worktree_ownership(
    *,
    repo_root: Path,
    worktree_path: Path,
    expected_issue_number: int,
    branch_name: str | None = None,
) -> WorktreeOwnership:
    """Classify issue-worktree ownership from repo/path/branch evidence."""

    resolved_root = repo_root.resolve()
    resolved_worktree = worktree_path.resolve(strict=False)
    if not resolved_worktree.is_relative_to(resolved_root):
        return WorktreeOwnership.OUTSIDE_REPOSITORY

    issue_number = None
    if branch_name:
        issue_number = issue_number_from_branch_name(branch_name)
    if issue_number is None:
        issue_number = issue_number_from_worktree_path(resolved_worktree)

    if issue_number is None:
        return WorktreeOwnership.FOREIGN_BRANCH
    if issue_number != expected_issue_number:
        return WorktreeOwnership.FOREIGN_ISSUE
    return WorktreeOwnership.OWNED
