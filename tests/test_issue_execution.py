from __future__ import annotations

import io
import os
import sys
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
    agent = worktree / ".github" / "agents" / "task-harness-orchestrator.agent.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("---\\nname: task-harness-orchestrator\\n---\\n")
    from task_harness.cli import _template_root
    hooks_dir = worktree / ".github" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / "task-harness.json").write_bytes(
        (_template_root() / "hooks" / "task-harness.json").read_bytes()
    )
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

    def inspect(_root, issue, *, expected_repo=None, expected_head=None, allow_dirty=False):
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


def test_existing_completed_run_requires_explicit_resume(controlled_issue):
    root, wt, preflight = controlled_issue
    with execution._guard(root, 1) as path:
        execution._save(path, {
            "run_id": "old", "status": "SESSION_EXITED", "session_id": "12345678-1234-4123-8123-123456789abc",
            "issue": 1, "repo": preflight.repository, "pr": 2,
            "head": preflight.head_sha, "worktree": str(wt),
        })
    assert execution.issue_run(root, 1)["verdict"] == "BLOCKED_REQUIRES_RESUME"
    plan = execution.issue_run(root, 1, resume=True)
    assert plan["verdict"] == "RESUME_DRY_RUN"
    assert plan["session_id"] == "12345678-1234-4123-8123-123456789abc"


def test_missing_session_id_cannot_be_guessed(controlled_issue):
    root, wt, preflight = controlled_issue
    with execution._guard(root, 1) as path:
        execution._save(path, {
            "run_id": "legacy", "status": "SESSION_EXITED",
            "issue": 1, "repo": preflight.repository, "pr": 2,
            "head": preflight.head_sha, "worktree": str(wt),
        })
    assert execution.issue_run(root, 1, resume=True)["verdict"] == "BLOCKED_SESSION_ID_UNKNOWN"


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
    original_is_file = Path.is_file
    monkeypatch.setattr(
        Path, "is_file",
        lambda self: True if str(self) == "/proc/self/stat" else original_is_file(self),
    )
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
    def git_query(cwd: Path, *cmd: str) -> str:
        assert cwd == worktree
        if cmd == ("git", "rev-parse", "HEAD"):
            return HEAD
        if cmd == ("git", "status", "--porcelain"):
            return ""
        raise AssertionError(f"unexpected prelaunch git query: {cmd}")

    monkeypatch.setattr(issue_preflight, "_run", git_query)
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
    assert invocations[0][1]["env"]["GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS"] == "true"
    assert result["require_subagent_hooks"] is True
    assert invocations[0][0][0] == "copilot"
    assert execution.issue_status(root, 1)["status"] == "SESSION_EXITED"
    assert execution._valid_session_id(result["session_id"])
    assert invocations[0][0][-1] == f'--session-id={result["session_id"]}'
    assert result["attempt"] == 1


def test_cli_status_does_not_mutate_files(controlled_issue, capsys):
    from task_harness.cli import main
    root, _, _ = controlled_issue
    assert main(["issue-status", "--root", str(root), "--issue", "1"]) == 0
    assert '"status": "NOT_STARTED"' in capsys.readouterr().out
    assert not (root / ".copilot-workflow").exists()


def test_missing_orchestrator_blocks_launch(controlled_issue):
    root, worktree, _ = controlled_issue
    (worktree / ".github" / "agents" / "task-harness-orchestrator.agent.md").unlink()
    with pytest.raises(execution.IssueExecutionError, match="orchestrator agent is missing"):
        execution.issue_run(root, 1, execute=True)
    assert not (root / ".copilot-workflow").exists()


def test_issue_run_default_does_not_require_copilot_binary(controlled_issue, monkeypatch):
    root, _, _ = controlled_issue
    monkeypatch.setattr(execution.shutil, "which", lambda _tool: None)
    result = execution.issue_run(root, 1)
    assert result["verdict"] == "DRY_RUN"
    with pytest.raises(execution.IssueExecutionError, match="Copilot CLI missing"):
        execution.issue_run(root, 1, execute=True)
    assert not (root / ".copilot-workflow").exists()

def test_reconcile_running_orphan_without_group_proof_is_blocked(controlled_issue, monkeypatch):
    root, _, _ = controlled_issue
    with execution._guard(root, 1) as path:
        execution._save(path, {
            "run_id": "orphan", "status": "RUNNING", "pid": 8123,
            "pid_start": "111", "boot_id": "same-host",
        })
    monkeypatch.setattr(execution, "_same_process", lambda _state: False)
    monkeypatch.setattr(execution, "_boot_id", lambda: "same-host")
    monkeypatch.setattr(execution, "_group_absent", lambda _pid: False)
    result = execution.issue_reconcile(root, 1)
    assert result["recovery"] == "BLOCKED_PROCESS_GROUP_UNVERIFIED"
    assert execution.issue_status(root, 1)["status"] == "RUNNING"


def test_reconcile_absent_group_to_interrupted_enables_explicit_resume(controlled_issue, monkeypatch):
    root, wt, preflight = controlled_issue
    session_id = "12345678-1234-4123-8123-123456789abc"
    with execution._guard(root, 1) as path:
        execution._save(path, {
            "run_id": "orphan", "status": "RUNNING", "pid": 8123,
            "pid_start": "111", "boot_id": "same-host", "session_id": session_id,
            "issue": 1, "repo": preflight.repository, "pr": preflight.pr_number,
            "head": preflight.head_sha, "worktree": str(wt),
        })
    monkeypatch.setattr(execution, "_same_process", lambda _state: False)
    monkeypatch.setattr(execution, "_boot_id", lambda: "same-host")
    monkeypatch.setattr(execution, "_group_absent", lambda _pid: True)
    result = execution.issue_reconcile(root, 1)
    assert result["status"] == "INTERRUPTED"
    assert result["recovery"] == "INTERRUPTED_CONFIRMED"
    assert result["exit_code"] is None
    plan = execution.issue_run(root, 1, resume=True)
    assert plan["verdict"] == "RESUME_DRY_RUN"
    assert plan["session_id"] == session_id


def test_starting_state_without_pid_cannot_be_reconciled(controlled_issue, monkeypatch):
    root, _, _ = controlled_issue
    with execution._guard(root, 1) as path:
        execution._save(path, {"run_id": "starting", "status": "STARTING", "pid": None})
    monkeypatch.setattr(execution, "_same_process", lambda _state: False)
    state = execution.issue_reconcile(root, 1)
    assert state["recovery"] == "BLOCKED_STARTING_PID_UNKNOWN"
    assert state["status"] == "STARTING"


def test_reboot_recovers_old_pid_identity_only(controlled_issue, monkeypatch):
    root, _, _ = controlled_issue
    with execution._guard(root, 1) as path:
        execution._save(path, {
            "run_id": "old", "status": "RUNNING", "pid": 8123,
            "pid_start": "111", "boot_id": "previous-host",
        })
    monkeypatch.setattr(execution, "_same_process", lambda _state: False)
    monkeypatch.setattr(execution, "_boot_id", lambda: "current-host")
    result = execution.issue_reconcile(root, 1)
    assert result["status"] == "INTERRUPTED"
    assert "reboot confirmed" in result["reason"]


def test_bounded_append_with_oversized_chunk_never_exceeds_limit():
    stream = io.BytesIO()
    execution._bounded_append(stream, b"a" * execution._MAX_LOG_BYTES)
    execution._bounded_append(stream, b"b" * (execution._MAX_LOG_BYTES + 65536))
    assert len(stream.getvalue()) == execution._MAX_LOG_BYTES
    assert stream.getvalue() == b"b" * execution._MAX_LOG_BYTES


@pytest.mark.skipif(not Path("/proc/self/stat").is_file(), reason="Linux /proc required")
def test_real_fake_copilot_process_and_same_session_resume(controlled_issue, monkeypatch, tmp_path):
    root, wt, preflight = controlled_issue
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "copilot"
    fake.write_text(
        "#!" + sys.executable + "\n"
        "import json,sys,time\n"
        "print(json.dumps(sys.argv[1:]), flush=True)\n"
        "time.sleep(0.4)\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])
    # Legacy request billing, then an explicit switch to AI credits on
    # resume of the SAME persisted parent Copilot session.
    monkeypatch.setenv("COPILOT_BILLING_MODE", "legacy_premium_requests")
    monkeypatch.delenv("COPILOT_MAX_AI_CREDITS", raising=False)
    from task_harness import issue_preflight
    monkeypatch.setattr(
        issue_preflight, "_remote_head",
        lambda _root, branch: preflight.main_sha if branch == "main" else preflight.head_sha,
    )
    def fake_git(cwd, *cmd):
        assert cwd == wt
        if cmd == ("git", "rev-parse", "HEAD"):
            return preflight.head_sha
        if cmd == ("git", "status", "--porcelain"):
            return ""
        raise AssertionError(cmd)
    monkeypatch.setattr(issue_preflight, "_run", fake_git)

    first = execution.issue_run(root, 1, execute=True)
    assert first["status"] == "SESSION_EXITED"
    assert first["attempt"] == 1
    session = first["session_id"]
    logged_args = __import__("json").loads(Path(first["log"]).read_text())
    assert f"--session-id={session}" in logged_args
    assert not any(arg.startswith("--max-ai-credits=") for arg in logged_args)
    assert first["billing_policy"]["unit"] == "PREMIUM_REQUESTS"
    assert first["billing_policy"]["provider_usage_verified"] is False

    # A restarted parent process must not discard earlier delegated-role
    # evidence from the SAME parent Copilot session.
    with execution._guard(root, 1) as state_path:
        ledger = execution._load(state_path)
        ledger["role_checkpoint"] = {"test_marker": "preserved"}
        execution._save(state_path, ledger)
    monkeypatch.setenv("COPILOT_BILLING_MODE", "ai_credits")
    monkeypatch.setenv("COPILOT_MAX_AI_CREDITS", "30")
    second = execution.issue_run(root, 1, execute=True, resume=True)
    assert second["role_checkpoint"] == {"test_marker": "preserved"}
    assert second["status"] == "SESSION_EXITED"
    assert second["attempt"] == 2
    assert second["previous_run_id"] == first["run_id"]
    assert second["session_id"] == session
    assert second["run_id"] != first["run_id"]
    args = __import__("json").loads(Path(second["log"]).read_text())
    assert f"--session-id={session}" in args
    assert "--max-ai-credits=30" in args
    assert second["billing_policy"]["unit"] == "AI_CREDITS"
    assert second["billing_policy"]["provider_usage_verified"] is False
    assert Path(first["log"]).exists()  # Previous run transcript preserved.


@pytest.mark.parametrize("tamper", ["removed", "changed", "unknown_extra", "symlink"])
def test_issue_execute_blocks_untrusted_hook_configs_before_copilot(
    controlled_issue, monkeypatch, tamper,
):
    root, worktree, _ = controlled_issue
    monkeypatch.setattr(execution.shutil, "which", lambda _: "/bin/copilot")
    hooks = worktree / ".github" / "hooks"
    config = hooks / "task-harness.json"
    if tamper == "removed":
        config.unlink()
    elif tamper == "changed":
        config.write_text('{"version":1,"hooks":{}}', encoding="utf-8")
    elif tamper == "unknown_extra":
        (hooks / "unreviewed.json").write_text(
            '{"hooks":{"subagentStart":[{"type":"command","bash":"echo untrusted"}]}}'
        )
    elif tamper == "symlink":
        original = hooks / "original.json"
        original.write_bytes(config.read_bytes())
        config.unlink()
        config.symlink_to(original)
    monkeypatch.setattr(
        execution.subprocess, "Popen",
        lambda *_args, **_kwargs: pytest.fail("unsafe config started Copilot"),
    )
    with pytest.raises(execution.IssueExecutionError, match="hook"):
        execution.issue_run(
            root, 1, repo="tiroq/agent-harness",
            expected_head=HEAD, execute=True,
        )
    assert not (root / ".copilot-workflow").exists()
