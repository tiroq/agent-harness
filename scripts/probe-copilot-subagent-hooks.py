#!/usr/bin/env python3
"""Isolated opt-in real Copilot CLI subagent-hook probe.

Default mode creates a disposable repository and validates its configuration
without spending AI credits. --live makes ONE bounded Copilot CLI invocation.
No Issue worktree, ledger, PR, or repository files are mutated by the probe.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK_HANDLER = r'''#!/usr/bin/env python3
import hashlib
import json
import os
import sys
from pathlib import Path

kind = sys.argv[1]
record = {"event": kind}
try:
    event = json.load(sys.stdin)
    if not isinstance(event, dict):
        raise ValueError("not an object")
    record["agentName"] = event.get("agentName")
    session_id = event.get("sessionId")
    record["sessionHash"] = hashlib.sha256(
        session_id.encode("utf-8")
    ).hexdigest()[:16] if isinstance(session_id, str) else None
    record["cwdMatches"] = event.get("cwd") == os.getcwd()
    if kind == "subagentStop":
        agent_id = event.get("agentId")
        record["agentIdPresent"] = bool(agent_id)
        record["agentIdHash"] = hashlib.sha256(
            agent_id.encode("utf-8")
        ).hexdigest()[:16] if isinstance(agent_id, str) else None
        record["stopReason"] = event.get("stopReason")
except (ValueError, UnicodeError) as exc:
    record["error"] = type(exc).__name__
path = Path(".github/hooks/probe-events.jsonl")
with path.open("a", encoding="utf-8") as sink:
    sink.write(json.dumps(record, sort_keys=True) + "\n")
print("{}")
'''

COORDINATOR = """---
name: hook-probe-coordinator
description: Read-only test coordinator that delegates exactly one internal role.
tools: [task]
---
Use the internal task tool exactly once to invoke the custom agent
hook-probe-worker. Ask it to reply with PROBE_WORKER_OK.
Do not call shell, write, git, network, or any other tool.
Then answer PROBE_COORDINATOR_DONE.
"""

WORKER = """---
name: hook-probe-worker
description: An inert, read-only internal subagent for testing Copilot hooks.
tools: []
---
Answer with the exact text PROBE_WORKER_OK. Do nothing else.
"""

PROMPT = (
    "Smoke test: invoke the custom agent hook-probe-worker through the "
    "internal task tool EXACTLY ONCE, ask it to reply PROBE_WORKER_OK, "
    "then reply PROBE_COORDINATOR_DONE. "
    "Do not read or change any files or call any other tools."
)


def create_fixture(root: Path) -> None:
    hooks = root / ".github" / "hooks"
    agents = root / ".github" / "agents"
    hooks.mkdir(parents=True)
    agents.mkdir(parents=True)
    (agents / "hook-probe-coordinator.agent.md").write_text(COORDINATOR, encoding="utf-8")
    (agents / "hook-probe-worker.agent.md").write_text(WORKER, encoding="utf-8")
    handler = hooks / "probe-handler.py"
    handler.write_text(HOOK_HANDLER, encoding="utf-8")
    data = {
        "version": 1,
        "hooks": {
            kind: [{
                "type": "command",
                "bash": f"python3 .github/hooks/probe-handler.py {kind}",
                "timeoutSec": 10,
            }]
            for kind in ("subagentStart", "subagentStop")
        },
    }
    (hooks / "probe.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def summarize(events: list[dict], exit_code: int | None) -> dict:
    relevant = [e for e in events if e.get("agentName") == "hook-probe-worker"]
    starts = [e for e in relevant if e.get("event") == "subagentStart"]
    stops = [e for e in relevant if e.get("event") == "subagentStop"]
    reasons = []
    if exit_code != 0:
        reasons.append("Copilot process did not exit successfully")
    if len(starts) != 1 or len(stops) != 1:
        reasons.append("expected exactly one subagentStart and one subagentStop for worker")
    if len(starts) == len(stops) == 1:
        a, b = starts[0], stops[0]
        if not (a.get("cwdMatches") and b.get("cwdMatches")):
            reasons.append("hook cwd does not match isolated repo")
        if not a.get("sessionHash") or a.get("sessionHash") != b.get("sessionHash"):
            reasons.append("parent sessionId absent or inconsistent")
        if not b.get("agentIdPresent"):
            reasons.append("subagentStop.agentId absent")
        if b.get("stopReason") != "end_turn":
            reasons.append("subagentStop did not end normally")
        if "error" in a or "error" in b:
            reasons.append("hook payload malformed")
        if events.index(a) >= events.index(b):
            reasons.append("subagentStop occurred before subagentStart")
    return {
        "verdict": "PASS" if not reasons else "FAIL",
        "reason": reasons,
        "copilot_exit_code": exit_code,
        "event_count": len(events),
        "worker_start_count": len(starts),
        "worker_stop_count": len(stops),
        "worker_agent_id_hash": stops[0].get("agentIdHash") if stops else None,
        "same_parent_session": (
            starts[0].get("sessionHash") == stops[0].get("sessionHash")
            if starts and stops else False
        ),
    }


def run_copilot(root: Path, *, model: str, timeout_sec: int) -> tuple[int | None, bool]:
    command = [
        "copilot", "-p", PROMPT,
        "--agent=hook-probe-coordinator",
        "--model", model,
        "--available-tools=task",
        "--deny-tool=shell",
        "--deny-tool=write",
        "--deny-tool=url",
        "--disable-builtin-mcps",
        "--no-ask-user",
        "--no-auto-update",
        "--max-ai-credits=2",
    ]
    env = dict(os.environ)
    # Repository hooks are disabled by default for untrusted -p sessions.
    env["GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS"] = "true"
    log = root / "copilot-output.log"
    timed_out = False
    with log.open("wb") as sink:
        proc = subprocess.Popen(
            command, cwd=root, env=env, stdout=sink, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
        try:
            exit_code = proc.wait(timeout=timeout_sec)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
            exit_code = None
        except KeyboardInterrupt:
            # Prevent orphaned Copilot subprocesses if the operator
            # interrupts the smoke probe in an interactive terminal.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait()
            raise
    return exit_code, timed_out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true",
                        help="Opt in to ONE real Copilot model request (may use paid AI credits)")
    parser.add_argument("--model", default="gpt-5.3-codex")
    parser.add_argument("--timeout-sec", type=int, default=120)
    parser.add_argument("--keep", action="store_true",
                        help="Retain private temporary fixture/logs for manual inspection")
    args = parser.parse_args(argv)
    if not 10 <= args.timeout_sec <= 180:
        parser.error("timeout must be between 10 and 180 seconds")
    if args.live and shutil.which("copilot") is None:
        print("BLOCKED: copilot executable is not installed/on PATH", file=sys.stderr)
        return 4
    tmp = Path(tempfile.mkdtemp(prefix="ah-subagent-hook-probe-"))
    os.chmod(tmp, 0o700)
    preserve_artifacts = args.keep
    try:
        create_fixture(tmp)
        if not args.live:
            print(json.dumps({
                "verdict": "DRY_RUN", "real_copilot_executed": False,
                "model": args.model, "fixture": str(tmp) if args.keep else "(temporary)",
                "next": "Pass --live for one bounded read-only Copilot subagent request",
            }, indent=2))
            return 0
        # Keep this early, flushed message even if the CLI or terminal dies.
        print("LIVE_PROBE_START: running isolated Copilot subprocess; logs are private",
              file=sys.stderr, flush=True)
        exit_code, timed_out = run_copilot(tmp, model=args.model, timeout_sec=args.timeout_sec)
        event_file = tmp / ".github" / "hooks" / "probe-events.jsonl"
        events = []
        if event_file.is_file():
            for line in event_file.read_text(encoding="utf-8").splitlines():
                try:
                    events.append(json.loads(line))
                except ValueError:
                    events.append({"error": "invalid event log JSON"})
        result = summarize(events, exit_code)
        if timed_out:
            result["verdict"] = "FAIL"
            result["reason"].append("Copilot timed out; process group was stopped")
        print(json.dumps(result, indent=2, sort_keys=True))
        if result["verdict"] != "PASS":
            preserve_artifacts = True
            print("Probe returned FAIL. Private diagnostic files are retained, "
                  "including copilot-output.log.", file=sys.stderr)
        return 0 if result["verdict"] == "PASS" else 4
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        # No traceback and no silent loss of the failing CLI's diagnostics.
        preserve_artifacts = True
        print(f"PROBE_ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 4
    except KeyboardInterrupt:
        preserve_artifacts = True
        print("PROBE_INTERRUPTED: child group stopped; diagnostics retained",
              file=sys.stderr)
        return 130
    finally:
        if preserve_artifacts:
            print(f"Private probe artifacts: {tmp}", file=sys.stderr, flush=True)
            print("Do not share complete copilot-output.log without redacting "
                  "credentials or private information.", file=sys.stderr)
        else:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    raise SystemExit(main())
