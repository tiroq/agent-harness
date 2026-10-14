"""Real Linux process-group regression tests with a hermetic fake Copilot.

No network, GitHub mutation, premium Copilot CLI, or external account needed.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from task_harness.issue_execution import (
    _group_absent, _paths, _same_process, issue_cancel, issue_reconcile,
)

pytestmark = pytest.mark.skipif(
    not Path("/proc/self/stat").is_file(),
    reason="Linux /proc and process groups required",
)

# Run the REAL issue_execution.issue_run as a subprocess launcher. Only its
# GitHub preflight/remote shell lookups are replaced with deterministic data.
_LAUNCH_SCRIPT = """
import sys
from pathlib import Path
from task_harness import issue_execution as execution, issue_preflight
from task_harness.issue_preflight import IssuePreflightResult
root=Path(sys.argv[1])
worktree=root/'.worktree'/'issue-1'
HEAD='a'*40
MAIN='b'*40
snapshot=IssuePreflightResult(
    repository='tiroq/agent-harness', issue_number=1, issue_title='test',
    worktree=worktree, branch='feat/1-fake', head_sha=HEAD,
    main_sha=MAIN, pr_number=2, comment_count=0, latest_comment_id=None,
    packet='# Hermetic fake test',
)
execution.inspect_issue=lambda *a,**kw: snapshot
issue_preflight._remote_head=lambda root,branch: MAIN if branch=='main' else HEAD
def git_query(cwd,*args):
    if args==('git','rev-parse','HEAD'): return HEAD
    if args==('git','status','--porcelain'): return ''
    raise RuntimeError('unexpected git query')
issue_preflight._run=git_query
execution.issue_run(root,1,repo='tiroq/agent-harness',expected_head=HEAD,execute=True)
"""


def _start_fake_parent(tmp_path: Path) -> tuple[Path, subprocess.Popen, int]:
    root=tmp_path/"repo"
    worktree=root/".worktree"/"issue-1"
    agent=worktree/".github"/"agents"/"task-harness-orchestrator.agent.md"
    agent.parent.mkdir(parents=True)
    agent.write_text("name: task-harness-orchestrator\n", encoding="utf-8")
    binpath=tmp_path/"bin"
    binpath.mkdir()
    fake=binpath/"copilot"
    fake.write_text(
        "#!"+sys.executable+"\n"
        "import signal,time\n"
        "print('FAKE CHILD RUNNING', flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    env=os.environ.copy()
    env["PATH"]=str(binpath)+os.pathsep+env.get("PATH","")
    env["PYTHONPATH"]=str(Path(__file__).resolve().parents[1]/"src")
    parent=subprocess.Popen(
        [sys.executable,"-c",_LAUNCH_SCRIPT,str(root)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        env=env, start_new_session=True,
    )
    state_path,_=_paths(root,1)
    child=0
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        if state_path.exists():
            try:
                state=json.loads(state_path.read_text())
                if state.get("status")=="RUNNING" and state.get("pid",0)>1:
                    child=state["pid"]
                    break
            except (OSError,ValueError,KeyError):
                pass
        if parent.poll() is not None:
            err=parent.stderr.read().decode(errors="replace") if parent.stderr else ""
            pytest.fail("fake session launcher exited before RUNNING: "+err[-1000:])
        time.sleep(.025)
    if not child:
        parent.kill()
        parent.wait(timeout=5)
        pytest.fail("fake launcher did not record child PID")
    return root,parent,child


def _cleanup(parent: subprocess.Popen, child: int) -> None:
    if child>1:
        try:
            os.killpg(child,signal.SIGKILL)
        except ProcessLookupError:
            pass
    if parent.poll() is None:
        parent.kill()
    parent.wait(timeout=5)
    if parent.stderr:
        parent.stderr.close()


def test_real_parent_sigkill_does_not_abandon_live_child(tmp_path: Path):
    root,parent,child=_start_fake_parent(tmp_path)
    try:
        state_path,_=_paths(root,1)
        state=json.loads(state_path.read_text())
        assert _same_process(state)
        os.kill(parent.pid,signal.SIGKILL)
        parent.wait(timeout=5)
        assert not _group_absent(child), "fake Copilot group must outlive launcher"
        recovery=issue_reconcile(root,1)
        assert recovery["status"]=="RUNNING"
        assert recovery["recovery"] in {
            "PROCESS_STILL_RUNNING","BLOCKED_PROCESS_GROUP_UNVERIFIED",
        }
        assert json.loads(state_path.read_text())["status"]=="RUNNING"
        # No automatic second session is allowed while a live/orphaned
        # group is unresolved.
    finally:
        _cleanup(parent,child)


def test_real_group_cancel_marks_session_cancelled(tmp_path: Path):
    root,parent,child=_start_fake_parent(tmp_path)
    try:
        state=issue_cancel(root,1)
        assert state["status"]=="CANCEL_REQUESTED"
        parent.wait(timeout=10)
        ledger=json.loads(_paths(root,1)[0].read_text())
        assert ledger["status"]=="CANCELLED"
        assert ledger["exit_code"] != 0
    finally:
        _cleanup(parent,child)
