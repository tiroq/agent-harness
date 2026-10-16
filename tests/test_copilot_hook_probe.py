"""Offline tests for the opt-in real Copilot CLI hook probe.

Tests use only a fake executable; --live with a real Copilot is operator-only.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
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
        "import sys\n"
        "if '--help' in sys.argv:\n"
        "    print('--agent --model --available-tools --deny-tool --disable-builtin-mcps --no-ask-user --no-auto-update --max-ai-credits'); sys.exit(0)\n"
        "if '--version' in sys.argv:\n"
        "    print('1.0.96'); sys.exit(0)\n"
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
        [sys.executable, str(PROBE), "--live", "--billing-mode", "ai_credits",
         "--timeout-sec", "15"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        timeout=20, check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["verdict"] == "PASS"
    assert data["worker_start_count"] == 1
    assert data["worker_stop_count"] == 1
    assert data["same_parent_session"] is True


def test_probe_failure_retains_private_cli_diagnostics(tmp_path):
    fake = tmp_path / "copilot"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--help' in sys.argv:\n"
        "    print('--agent --model --available-tools --deny-tool --disable-builtin-mcps --no-ask-user --no-auto-update --max-ai-credits'); sys.exit(0)\n"
        "if '--version' in sys.argv:\n"
        "    print('1.0.96'); sys.exit(0)\n"
        "import sys\n"
        "print('FAKE_COPILOT_UNSUPPORTED_OPTION', flush=True)\n"
        "sys.exit(2)\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    result = subprocess.run(
        [sys.executable, str(PROBE), "--live", "--billing-mode", "ai_credits",
         "--timeout-sec", "15"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
        timeout=20, check=False,
    )
    assert result.returncode == 4
    assert result.stdout.startswith("{")
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "FAIL"
    assert "LIVE_PROBE_START" in result.stderr
    marker = "Private probe artifacts: "
    assert marker in result.stderr
    folder = Path(result.stderr.split(marker, 1)[1].splitlines()[0].strip())
    try:
        assert (folder / "copilot-output.log").read_text().strip() == (
            "FAKE_COPILOT_UNSUPPORTED_OPTION"
        )
        assert (folder / ".github" / "hooks" / "probe.json").exists()
    finally:
        shutil.rmtree(folder)



def test_live_cli_uses_supported_soft_credit_limit_and_bounded_toolset():
    probe = _module()
    import inspect
    source = inspect.getsource(probe.run_copilot)
    assert '"--max-ai-credits=30"' in source
    assert '"--max-ai-credits=2"' not in source
    assert '"--available-tools=task"' in source
    assert '"--deny-tool=shell"' in source
    assert '"--deny-tool=write"' in source
    assert '"--disable-builtin-mcps"' in source



def test_live_probe_requires_explicit_billing_mode_before_cli_invocation(tmp_path):
    # A fake executable is present, but --live without a billing mode must
    # fail during argparse before calling it or spending any premium request.
    fake = tmp_path / "copilot"
    sentinel = tmp_path / "was-executed"
    fake.write_text(
        "#!/bin/sh\n"
        f"touch {sentinel}\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    result = subprocess.run(
        [sys.executable, str(PROBE), "--live"],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode == 2
    assert "--billing-mode" in result.stderr
    assert not sentinel.exists()


def test_probe_legacy_mode_never_applies_ai_credit_flag(tmp_path):
    fake = tmp_path / "copilot"
    args_path = tmp_path / "args.json"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--help' in sys.argv:\n"
        "    print('--agent --model --available-tools --deny-tool --disable-builtin-mcps --no-ask-user --no-auto-update --max-ai-credits'); sys.exit(0)\n"
        "if '--version' in sys.argv:\n"
        "    print('1.0.96'); sys.exit(0)\n"
        "import json, os, sys, subprocess\n"
        "from pathlib import Path\n"
        f"Path({str(args_path)!r}).write_text(json.dumps(sys.argv[1:]))\n"
        "common={'sessionId':'simulated-parent','cwd':os.getcwd(),"
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
    result = subprocess.run(
        [sys.executable, str(PROBE), "--live", "--billing-mode",
         "legacy_premium_requests", "--timeout-sec", "15"],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["verdict"] == "PASS"
    argv = json.loads(args_path.read_text())
    assert not any(s.startswith("--max-ai-credits") for s in argv)


def test_preflight_checks_installed_cli_without_launching_model(tmp_path):
    fake = tmp_path / "copilot"
    sentinel = tmp_path / "model-requested"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--help' in sys.argv:\n"
        "    print('--agent --model --available-tools --deny-tool --disable-builtin-mcps --no-ask-user --no-auto-update --max-ai-credits'); sys.exit(0)\n"
        "if '--version' in sys.argv:\n"
        "    print('1.0.96'); sys.exit(0)\n"
        f"open({str(sentinel)!r}, 'w').close()\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    result = subprocess.run(
        [sys.executable, str(PROBE), "--preflight", "--billing-mode", "ai_credits"],
        env={**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]},
        capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "PASS"
    assert payload["cli_version"] == "1.0.96"
    assert payload["model_executed"] is False
    assert payload["usage_verified"] is False
    assert not sentinel.exists()


def test_preflight_blocks_missing_credit_cap_without_paid_cli_request(tmp_path):
    fake = tmp_path / "copilot"
    sentinel = tmp_path / "model-requested"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "if '--help' in sys.argv:\n"
        "    print('--agent --model --available-tools --deny-tool --disable-builtin-mcps --no-ask-user --no-auto-update'); sys.exit(0)\n"
        "if '--version' in sys.argv:\n"
        "    print('1.0.50'); sys.exit(0)\n"
        f"open({str(sentinel)!r}, 'w').close()\n",
        encoding="utf-8",
    )
    fake.chmod(0o700)
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    for mode, expected_code in (("ai_credits", 4), ("legacy_premium_requests", 0)):
        result = subprocess.run(
            [sys.executable, str(PROBE), "--preflight", "--billing-mode", mode],
            env=env, capture_output=True, text=True, check=False, timeout=20,
        )
        assert result.returncode == expected_code, result.stderr
        payload = json.loads(result.stdout)
        assert payload["verdict"] == ("BLOCKED" if expected_code else "PASS")
        if expected_code:
            assert "--max-ai-credits" in payload["missing_flags"]
        assert not sentinel.exists()
    result = subprocess.run(
        [sys.executable, str(PROBE), "--live", "--billing-mode", "ai_credits"],
        env=env, capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode == 4
    assert json.loads(result.stdout)["reason"] == "CLI_FLAGS_UNVERIFIED"
    assert not sentinel.exists()
