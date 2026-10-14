"""Offline tests for the opt-in real Copilot CLI hook probe.

Tests use only a fake executable; --live with a real Copilot is operator-only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


PROBE = Path(__file__).resolve().parents[1] / "scripts" / "probe-copilot-subagent-hooks.py"


def _module():
    spec = importlib.util.spec_from_file_location("hook_probe", PROBE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_probe_offline_default_never_invokes_copilot(tmp_path):
    result = subprocess.run(
        [sys.executable, str(PROBE)], cwd=tmp_path,
        capture_output=True, text=True, timeout=20, check=False,
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ.get("PATH", "")},
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "DRY_RUN"
    assert payload["real_copilot_executed"] is False


def test_probe_summary_requires_real_start_and_stop_provenance():
    probe = _module()
    base = {
        "agentName": "hook-probe-worker",
        "sessionHash": "one-parent",
        "cwdMatches": True,
    }
    start = dict(base, event="subagentStart")
    stop = dict(base, event="subagentStop", agentIdPresent=True,
                agentIdHash="opaque-agent-hash", stopReason="end_turn")
    assert probe.summarize([start, stop], 0)["verdict"] == "PASS"
    assert probe.summarize([start], 0)["verdict"] == "FAIL"
    assert probe.summarize([stop], 0)["verdict"] == "FAIL"
    assert probe.summarize([start, dict(stop, sessionHash="other-parent")], 0)["verdict"] == "FAIL"
    assert probe.summarize([start, dict(stop, agentIdPresent=False)], 0)["verdict"] == "FAIL"
    assert probe.summarize([start, dict(stop, stopReason="other")], 0)["verdict"] == "FAIL"
    assert probe.summarize([start, stop], 1)["verdict"] == "FAIL"


def test_probe_fake_cli_emits_two_events_in_isolated_repository(tmp_path):
    fake = tmp_path / "copilot"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json,subprocess\n"
        "common={'sessionId':'simulated-parent','cwd':__import__('os').getcwd(),"
        "'agentName':'hook-probe-worker'}\n"
        "for kind in ('subagentStart','subagentStop'):\n"
        "    event=dict(common)\n"
        "    if kind=='subagentStop':\n"
        "        event.update(agentId='simulated-worker-123',"
        "stopReason='end_turn')\n"
        "    subprocess.run(['python3','.github/hooks/probe-handler.py',kind],"
        "input=json.dumps(event),text=True,check=True,"
        "stdout=subprocess.DEVNULL)\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    result = subprocess.run(
        [sys.executable, str(PROBE), "--live", "--timeout-sec", "15"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["verdict"] == "PASS"
    assert data["worker_start_count"] == 1
    assert data["worker_stop_count"] == 1
    assert data["same_parent_session"] is True
