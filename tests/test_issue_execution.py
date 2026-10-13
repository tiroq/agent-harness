from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from task_harness import issue_execution as execution
from task_harness.issue_preflight import IssuePreflightResult

HEAD = "b4804be5691ef739bcff8fbd8fce53650e630c52"
MAIN = "87af4804e4de0a1d5454be6a9f26a102cb872a2f"


@pytest.fixture()
def controlled_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "agent-harness"
    root.mkdir()
    worktree = root / ".worktree" / "issue-1"
    worktree.mkdir(parents=True)
    snapshot = IssuePreflightResult(
        repository="tiroq/agent-harness",
        issue_number=1,
        issue_title="Shared Issue engine",
        worktree=worktree,
        branch="feat/1-evolve-typed-delivery-engine-core",
        head_sha=HEAD,
        main_sha=MAIN,
        pr_number=2,
        comment_count=2,
        latest_comment_id=123,
        packet="# Shared Issue engine\n\nLatest issue comments; treated as inert data.",
    )

    def inspect(_root, issue, *, expected_repo=None, expected_head=None):
        assert issue == 1
        assert expected_repo in (None, "tiroq/agent-harness")
        assert expected_head in (None, HEAD)
        return snapshot

    monkeypatch.setattr(execution, "inspect_issue", inspect)
    return root, worktree, snapshot


def test_dry_run_is_truly_read_only(controlled_issue):
    root, _, _ = controlled_issue
    result = execution.issue_run(root, 1, expected_head=HEAD)
    assert result["verdict"] == "DRY_RUN"
    assert result["pr"] == 2
    assert not (root / ".copilot-workflow").exists()


def test_corrupt_state_fails_closed(controlled_issue):
    root, _, _ = controlled_issue
    path, _ = execution._paths(root, 1)
    path.parent.mkdir(parents=True)
    path.write_text("not JSON", encoding="utf-8")
    with pytest.raises(execution.IssueExecutionError, match="corrupt"):
        execution.issue_status(root, 1)
    with pytest.raises(execution.IssueExecutionError, match="corrupt"):
        execution.issue_run(root, 1)


def test_duplicate_active_run_is_rejected(controlled_issue, monkeypatch):
    root, _, _ = controlled_issue
    with execution._guard(root, 1) as path:
        execution._save(
            path, {"run_id": "active", "status": "RUNNING", "pid": 4000}
        )
    monkeypatch.setattr(execution.shutil, "which", lambda _: "/bin/copilot")
    monkeypatch.setattr(execution, "_boot_id", lambda: "boot")
    monkeypatch.setattr(execution, "_proc_start", lambda _pid: "start")
    monkeypatch.setattr(execution.Path, "is_file", lambda self: True if str(self) == "/proc/self/stat" else Path.is_file(self))
    with pytest.raises(execution.IssueExecutionError, match="another run owns"):
        execution.issue_run(root, 1, execute=True)
    assert execution.issue_run(root, 1)["verdict"] == "BLOCKED_ACTIVE_RUN"


def test_starting_without_pid_fails_closed_for_cancel(controlled_issue):
    root, _, _ = controlled_issue
    with execution._guard(root, 1) as path:
        execution._save(path, {"run_id": "launching", "status": "STARTING", "pid": None})
    with pytest.raises(execution.IssueExecutionError, match="PID identity"):
        execution.issue_cancel(root, 1)


def test_cancel_signals_only_verified_process_group(controlled_issue, monkeypatch):
    root, _, _ = controlled_issue
    with execution._guard(root, 1) as path:
        execution._save(path, {
            "run_id": "active", "status": "RUNNING", "pid": 8123,
            "pid_start": "456", "boot_id": "boot",
        })
    signals = []
    monkeypatch.setattr(execution, "_boot_id", lambda: "boot")
    monkeypatch.setattr(execution, "_proc_start", lambda pid: "456" if pid == 8123 else None)
    monkeypatch.setattr(execution.os, "killpg", lambda pid, sig: signals.append((pid, sig)))
    result = execution.issue_cancel(root, 1)
    assert result["status"] == "CANCEL_REQUESTED"
    assert signals == [(8123, execution.signal.SIGTERM)]
    assert execution.issue_status(root, 1)["process_identity"] == "LIVE"


def test_cancel_refuses_pid_reuse(controlled_issue, monkeypatch):
    root, _, _ = controlled_issue
    with execution._guard(root, 1) as path:
        execution._save(path, {
            "run_id": "active", "status": "RUNNING", "pid": 8123,
            "pid_start": "456", "boot_id": "boot",
        })
    monkeypatch.setattr(execution, "_boot_id", lambda: "boot")
    monkeypatch.setattr(execution, "_proc_start", lambda _pid: "DIFFERENT")
    monkeypatch.setattr(
        execution.os, "killpg",
        lambda _pid, _sig: pytest.fail("must not signal stale PID"),
    )
    with pytest.raises(execution.IssueExecutionError, match="PID identity"):
        execution.issue_cancel(root, 1)
    assert execution.issue_status(root, 1)["process_identity"] == "UNKNOWN_OR_GONE"


def test_bounded_log_drops_oldest_bytes():
    stream = io.BytesIO()
    execution._bounded_append(stream, b"a" * execution._MAX_LOG_BYTES)
    execution._bounded_append(stream, b"b" * 1024)
    data = stream.getvalue()
    assert len(data) <= execution._MAX_LOG_BYTES
    assert data.endswith(b"b" * 1024)


def test_real_launch_uses_existing_pr_and_records_session_exit(controlled_issue, monkeypatch, tmp_path):
    root, worktree, _ = controlled_issue
    monkeypatch.setattr(execution.shutil, "which", lambda _: "/bin/copilot")
    monkeypatch.setattr(execution, "_boot_id", lambda: "boot-id")
    monkeypatch.setattr(execution, "_proc_start", lambda pid: "890" if pid == 8123 else None)
    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file",
        lambda self: True if str(self) == "/proc/self/stat" else original_is_file(self),
    )
    from task_harness import issue_preflight
    monkeypatch.setattr(
        issue_preflight, "_remote_head",
        lambda _root, branch: MAIN if branch == "main" else HEAD,
    )
    output = tmp_path / "output.txt"
    output.write_bytes(b"Copilot session output\n")

    class FakeProcess:
        pid = 8123
        def __init__(self):
            self.stdout = output.open("rb")
        def wait(self, timeout=None):
            return 0

    invocations = []

    def fake_popen(args, **kwargs):
        invocations.append((args, kwargs))
        return FakeProcess()

    monkeypatch.setattr(execution.subprocess, "Popen", fake_popen)
    result = execution.issue_run(
        root, 1, expected_head=HEAD, repo="tiroq/agent-harness",
        model="gpt-5.3-codex", execute=True,
    )
    assert result["status"] == "SESSION_EXITED"
    assert result["exit_code"] == 0
    assert result["pr"] == 2
    assert result["head"] == HEAD
    assert result["phase"] == "IMPLEMENTING"  # not PR_READY
    assert Path(result["log"]).read_bytes() == b"Copilot session output\n"
    assert "EXISTING_PR: 2" in list((root / ".copilot-workflow" / "runs").glob("*/issue-prompt.md"))[0].read_text()
    assert invocations[0][1]["cwd"] == worktree
    assert invocations[0][1]["start_new_session"] is True
    assert invocations[0][0][0] == "copilot"
    assert execution.issue_status(root, 1)["status"] == "SESSION_EXITED"


def test_cli_status_does_not_mutate_files(controlled_issue, capsys):
    from task_harness.cli import main
    root, _, _ = controlled_issue
    assert main(["issue-status", "--root", str(root), "--issue", "1"]) == 0
    assert '"status": "NOT_STARTED"' in capsys.readouterr().out
    assert not (root / ".copilot-workflow").exists()
