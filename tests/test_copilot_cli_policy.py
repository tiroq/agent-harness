from __future__ import annotations

import pytest

from task_harness.cli import main
from task_harness.core.copilot_cli import copilot_arguments


def test_copilot_policy_shared_for_issue_and_package_sessions(monkeypatch):
    monkeypatch.setenv("COPILOT_MAX_AUTOPILOT_CONTINUES", "17")
    args = copilot_arguments(model="gpt-5.3-codex", allow_all=True)
    assert args[:4] == [
        "copilot", "--agent=task-harness-orchestrator",
        "--model", "gpt-5.3-codex",
    ]
    assert "--autopilot" in args
    assert "--max-autopilot-continues=17" in args
    assert "--allow-all-tools" in args
    assert "--deny-tool=shell(git push)" in args
    assert "--deny-tool=shell(git merge)" in args
    assert "--deny-tool=shell(git reset)" in args
    assert "--deny-tool=shell(sudo)" in args
    assert "--deny-tool=shell(rm -rf)" in args
    narrowed = copilot_arguments(model="gpt-5.3-codex", allow_all=False)
    assert "--allow-all-tools" not in narrowed
    assert "--deny-tool=shell(git push)" in narrowed


@pytest.mark.parametrize(
    "argv",
    [
        ["issue-run", "--issue", "1", "--execute"],
        ["issue-run", "--issue", "1", "--execute", "--repo", "tiroq/agent-harness"],
        ["issue-run", "--issue", "1", "--execute", "--expected-head", "a" * 40],
        ["issue-run", "--issue", "1", "--execute", "--dry-run",
         "--repo", "tiroq/agent-harness", "--expected-head", "a" * 40],
    ],
)
def test_issue_launch_refuses_ambiguous_arguments(argv):
    with pytest.raises(SystemExit) as error:
        main(argv)
    assert error.value.code == 2
