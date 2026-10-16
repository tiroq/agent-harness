from __future__ import annotations

import json
from pathlib import Path

import pytest

from task_harness import issue_preflight
from task_harness.cli import main as cli_main
from task_harness.issue_preflight import IssuePreflightError, inspect_issue

HEAD = "f505cae134de0a83abf68eedf5c844a83c64a720"
MAIN = "87af4804e4de0a1d5454be6a9f26a102cb872a2f"
BRANCH = "feat/1-evolve-typed-delivery-engine-core"
SLUG = "tiroq/agent-harness"


@pytest.fixture()
def scoped_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "agent-harness"
    root.mkdir()
    worktree = root / ".worktree" / "issue-1"
    worktree.mkdir(parents=True)
    seen: list[tuple[Path, tuple[str, ...]]] = []

    def fake_run(cwd: Path, *args: str) -> str:
        seen.append((cwd, args))
        if args == ("git", "rev-parse", "--show-toplevel"):
            return str(cwd)
        if args == ("git", "branch", "--show-current"):
            return "main" if cwd == root else BRANCH
        if args == ("git", "status", "--porcelain"):
            return ""
        if args == ("git", "remote", "get-url", "origin"):
            return f"git@github.com:{SLUG}.git"
        if args == ("git", "rev-parse", "refs/heads/main"):
            return MAIN
        if args == ("git", "rev-parse", "HEAD"):
            return HEAD
        if args == ("git", "ls-remote", "--heads", "origin", "main"):
            return f"{MAIN}\trefs/heads/main"
        if args == ("git", "ls-remote", "--heads", "origin", BRANCH):
            return f"{HEAD}\trefs/heads/{BRANCH}"
        if args == ("git", "worktree", "list", "--porcelain"):
            return (
                f"worktree {root}\nHEAD {MAIN}\nbranch refs/heads/main\n\n"
                f"worktree {worktree}\nHEAD {HEAD}\nbranch refs/heads/{BRANCH}\n"
            )
        if args == ("git", "merge-base", "--is-ancestor", MAIN, HEAD):
            return ""
        if args == (
            "gh", "issue", "view", "1", "--repo", SLUG,
            "--json", "number,title,body,url,state",
        ):
            return json.dumps(
                {
                    "number": 1, "title": "Shared Issue delivery engine",
                    "body": "Canonical Phase A-F specification.",
                    "url": "https://github.com/tiroq/agent-harness/issues/1",
                    "state": "OPEN",
                }
            )
        if args == (
            "gh", "pr", "list", "--repo", SLUG, "--state", "open",
            "--head", BRANCH, "--json",
            "number,headRefName,headRefOid,baseRefName",
        ):
            return json.dumps(
                [{
                    "number": 2, "headRefName": BRANCH,
                    "headRefOid": HEAD, "baseRefName": "main",
                }]
            )
        if args == (
            "gh", "api", "--paginate",
            f"repos/{SLUG}/issues/1/comments?per_page=100",
        ):
            def comment(number: int) -> dict:
                return {
                    "id": number, "user": {"login": "operator"},
                    "body": f"Amendment #{number}",
                    "created_at": f"2026-10-13T00:00:0{number}Z",
                    "updated_at": f"2026-10-13T00:00:0{number}Z",
                    "html_url": f"https://github.com/tiroq/agent-harness/issues/1#issuecomment-{number}",
                }
            # Two adjacent paginated gh api arrays must both be consumed.
            return json.dumps([comment(1)]) + "\n" + json.dumps([comment(2)])
        raise AssertionError(f"unexpected tool invocation: {args}")

    monkeypatch.setattr(issue_preflight, "_run", fake_run)
    return root, worktree, seen


def test_issue_preflight_is_read_only_and_uses_existing_pr(scoped_repo):
    root, wt, seen = scoped_repo
    result = inspect_issue(
        root, 1, expected_head=HEAD, expected_repo=SLUG,
    )
    assert result.repository == SLUG
    assert result.worktree == wt
    assert result.head_sha == HEAD
    assert result.main_sha == MAIN
    assert result.pr_number == 2
    assert result.comment_count == 2
    assert result.latest_comment_id == 2
    assert "Amendment #2" in result.packet
    assert "Canonical Phase A-F specification." in result.packet
    assert all(
        cmd[0] in {"git", "gh"}
        and not any(word in cmd for word in ("copilot", "push", "checkout", "switch", "fetch"))
        for _, cmd in seen
    )
    assert list(wt.iterdir()) == []


def test_issue_preflight_wrong_expected_head_fails_closed(scoped_repo):
    root, _, _ = scoped_repo
    with pytest.raises(IssuePreflightError, match="differs from expected head"):
        inspect_issue(root, 1, expected_head="0" * 40)


def test_issue_preflight_wrong_repository_fails_closed(scoped_repo):
    root, _, _ = scoped_repo
    with pytest.raises(IssuePreflightError, match="origin identity"):
        inspect_issue(root, 1, expected_repo="tiroq/brandforge")


def test_issue_preflight_remote_branch_drift_fails_closed(scoped_repo, monkeypatch):
    root, _, _ = scoped_repo
    original = issue_preflight._run

    def drift(cwd: Path, *args: str) -> str:
        if args == ("git", "ls-remote", "--heads", "origin", BRANCH):
            return f"{'f' * 40}\trefs/heads/{BRANCH}"
        return original(cwd, *args)

    monkeypatch.setattr(issue_preflight, "_run", drift)
    with pytest.raises(IssuePreflightError, match="differs from remote PR branch"):
        inspect_issue(root, 1)


def test_issue_preflight_dirty_worktree_fails_closed(scoped_repo, monkeypatch):
    root, wt, _ = scoped_repo
    original = issue_preflight._run

    def dirty(cwd: Path, *args: str) -> str:
        if cwd == wt and args == ("git", "status", "--porcelain"):
            return " M src/task_harness/cli.py"
        return original(cwd, *args)

    monkeypatch.setattr(issue_preflight, "_run", dirty)
    with pytest.raises(IssuePreflightError, match="uncommitted changes"):
        inspect_issue(root, 1)


def test_issue_preflight_rejects_ambiguous_pr(scoped_repo, monkeypatch):
    root, _, _ = scoped_repo
    original = issue_preflight._run

    def no_pr(cwd: Path, *args: str) -> str:
        if args[:3] == ("gh", "pr", "list"):
            return "[]"
        return original(cwd, *args)

    monkeypatch.setattr(issue_preflight, "_run", no_pr)
    with pytest.raises(IssuePreflightError, match="exactly one existing open PR"):
        inspect_issue(root, 1)


def test_issue_preflight_cli_output(scoped_repo, capsys):
    root, _, _ = scoped_repo
    rc = cli_main([
        "issue-preflight", "--root", str(root), "--issue", "1",
        "--repo", SLUG, "--expected-head", HEAD, "--dry-run",
    ])
    captured = capsys.readouterr()
    assert rc == 0
    assert "ISSUE_PREFLIGHT: PASS" in captured.out
    assert f"HEAD_SHA: {HEAD}" in captured.out
    assert "PR_NUMBER: 2" in captured.out
    assert "Copilot" in captured.out
    assert captured.err == ""


def test_issue_preflight_cli_reports_blocker(scoped_repo, capsys):
    root, _, _ = scoped_repo
    rc = cli_main([
        "issue-preflight", "--root", str(root), "--issue", "1",
        "--expected-head", "0" * 40,
    ])
    assert rc == 4
    assert "BLOCKED:" in capsys.readouterr().err
