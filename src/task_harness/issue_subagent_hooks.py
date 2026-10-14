"""Bounded Copilot CLI subagent hooks -> existing parent Issue ledger.

Official Copilot CLI subagentStart/subagentStop command-hook payloads are
runtime observations, NOT cryptographic proof. This module cannot launch a
Copilot process, create an Issue, or grant PR readiness.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .core.delivery_pipeline import PipelineError
from .issue_execution import IssueExecutionError, _clock, _guard, _load, _save
from .issue_role_checkpoints import _parent, _checkpoint_state, _SUBAGENT_ID

_MAX_HOOK_INPUT = 2 * 1024 * 1024
_MAX_EVENTS = 12
_ALLOWED = frozenset({"subagentStart", "subagentStop"})
_ROLE_AGENTS = frozenset({
    "task-harness-implementer",
    "task-harness-reviewer",
    "task-harness-test-engineer",
    "task-harness-evidence-auditor",
    "task-harness-committer",
})


def ingest_hook_event(
    root: Path, issue: int, *, run_id: str, parent_session_id: str,
    event_name: str, raw: bytes,
) -> dict[str, Any]:
    """Fail closed for malformed hooks, but never infer completion from them."""
    if event_name not in _ALLOWED or not raw or len(raw) > _MAX_HOOK_INPUT:
        raise IssueExecutionError("unsupported or oversized Copilot hook event")
    try:
        event = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise IssueExecutionError("invalid Copilot hook JSON") from exc
    if not isinstance(event, dict):
        raise IssueExecutionError("Copilot hook JSON must be an object")
    if event_name == "subagentStart":
        required = ("sessionId", "agentName", "cwd")
    else:
        required = ("sessionId", "agentId", "agentName", "cwd", "stopReason")
    if any(not isinstance(event.get(key), str) or not event[key] for key in required):
        raise IssueExecutionError("Copilot hook event lacks required identity fields")
    if event_name == "subagentStop":
        if event["stopReason"] != "end_turn":
            raise IssueExecutionError("Copilot subagentStop did not end normally")
        if not _SUBAGENT_ID.fullmatch(event["agentId"]):
            raise IssueExecutionError("Copilot subagent ID has unsupported shape")

    with _guard(root, issue) as path:
        ledger = _parent(_load(path), issue=issue, run_id=run_id, session_id=parent_session_id)
        role_data = ledger.get("role_checkpoint")
        if not isinstance(role_data, dict) or role_data.get("before") is None:
            return {"status": "IGNORED_NO_ACTIVE_ROLE", "pr_ready": False}
        state = _checkpoint_state(role_data)
        ticket = state.pending
        if ticket is None:
            return {"status": "IGNORED_NO_ACTIVE_ROLE", "pr_ready": False}
        if event["sessionId"] != parent_session_id:
            raise IssueExecutionError("Copilot hook parent session mismatch")
        worktree = Path(ledger["worktree"]).resolve()
        if Path(event["cwd"]).resolve() != worktree:
            raise IssueExecutionError("Copilot hook cwd mismatch")
        expected_agent = "task-harness-" + ticket.role.value
        if event["agentName"] not in _ROLE_AGENTS:
            return {"status": "IGNORED_UNRELATED_AGENT", "pr_ready": False}
        if event["agentName"] != expected_agent:
            raise IssueExecutionError("Copilot hook role does not match active checkpoint")
        events = role_data.setdefault("hook_events", [])
        if not isinstance(events, list) or len(events) >= _MAX_EVENTS:
            raise IssueExecutionError("Copilot hook events invalid or overflow")
        fingerprint = hashlib.sha256(raw).hexdigest()
        if any(isinstance(item, dict) and item.get("sha256") == fingerprint for item in events):
            return {"status": "DUPLICATE_IGNORED", "pr_ready": False}
        if event_name == "subagentStart":
            if any(item.get("kind") == "subagentStart" for item in events):
                raise IssueExecutionError("repeated Copilot subagentStart for one checkpoint")
            record = {"kind": "subagentStart", "agent_name": expected_agent, "sha256": fingerprint}
        else:
            if not any(item.get("kind") == "subagentStart" for item in events):
                raise IssueExecutionError("Copilot subagentStop without matching start event")
            if any(item.get("kind") == "subagentStop" for item in events):
                raise IssueExecutionError("conflicting Copilot subagentStop for checkpoint")
            record = {
                "kind": "subagentStop", "agent_name": expected_agent,
                "agent_id": event["agentId"], "sha256": fingerprint,
                "response_sha256": hashlib.sha256(
                    event.get("response", "").encode("utf-8")
                ).hexdigest() if isinstance(event.get("response", ""), str) else None,
            }
        events.append(record)
        role_data["identity_source"] = "COPILOT_HOOK_OBSERVED_UNATTESTED"
        role_data["last_change"] = _clock()
        _save(path, ledger)
        return {
            "status": "HOOK_RECORDED", "event": event_name,
            "agent_name": expected_agent, "pr_ready": False,
            "identity_source": "COPILOT_HOOK_OBSERVED_UNATTESTED",
        }


def stopped_agent_id(role_data: dict[str, Any]) -> str | None:
    """Latest matching hook ID, or None when no hook was recorded."""
    records = role_data.get("hook_events", ())
    if not isinstance(records, list):
        raise IssueExecutionError("malformed Copilot hook ledger")
    stops = [r for r in records if isinstance(r, dict) and r.get("kind") == "subagentStop"]
    if len(stops) > 1:
        raise IssueExecutionError("conflicting Copilot agent identity events")
    return stops[0]["agent_id"] if stops else None
