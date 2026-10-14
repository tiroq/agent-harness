"""Shared, repository-neutral Copilot CLI invocation policy."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping




_BILLING_MODES = frozenset({"unconfigured", "legacy_premium_requests", "ai_credits"})
_MIN_AI_CREDITS = 30
_MAX_AI_CREDITS = 1000000


@dataclass(frozen=True, slots=True)
class CopilotBillingPolicy:
    """CLI invocation policy, not an authoritative GitHub billing meter.

    No automatic account-plan detection or conversion between request-based
    and token-based billing is available from the existing Copilot runner.
    """

    mode: str
    max_ai_credits: int | None

    @classmethod
    def from_environment(cls, env: Mapping[str, str] | None = None) -> "CopilotBillingPolicy":
        source = os.environ if env is None else env
        mode = source.get("COPILOT_BILLING_MODE", "unconfigured").strip().lower()
        if mode not in _BILLING_MODES:
            raise ValueError(
                "COPILOT_BILLING_MODE must be legacy_premium_requests or ai_credits "
                "(omit for unconfigured compatibility); never guess account billing"
            )
        raw = source.get("COPILOT_MAX_AI_CREDITS", "").strip()
        if mode != "ai_credits":
            if raw:
                raise ValueError(
                    "COPILOT_MAX_AI_CREDITS is only valid with "
                    "COPILOT_BILLING_MODE=ai_credits; premium requests are separate"
                )
            return cls(mode=mode, max_ai_credits=None)
        if not raw:
            raw = str(_MIN_AI_CREDITS)
        if not raw.isascii() or not raw.isdecimal():
            raise ValueError("COPILOT_MAX_AI_CREDITS must be an integer >= 30")
        credits = int(raw)
        if not _MIN_AI_CREDITS <= credits <= _MAX_AI_CREDITS:
            raise ValueError("COPILOT_MAX_AI_CREDITS must be between 30 and 1000000")
        return cls(mode=mode, max_ai_credits=credits)

    def flags(self) -> list[str]:
        return (
            [f"--max-ai-credits={self.max_ai_credits}"]
            if self.mode == "ai_credits" else []
        )

    def as_dict(self) -> dict[str, str | int | None]:
        return {
            "mode": self.mode,
            "unit": (
                "AI_CREDITS" if self.mode == "ai_credits"
                else "PREMIUM_REQUESTS" if self.mode == "legacy_premium_requests"
                else "UNKNOWN"
            ),
            "max_ai_credits_soft_limit": self.max_ai_credits,
            "provider_usage_verified": False,
        }


def copilot_arguments(
    *, model: str, allow_all: bool,
    billing_policy: CopilotBillingPolicy | None = None,
) -> list[str]:
    """Shared CLI policy; billing units are explicit, never converted."""
    policy = billing_policy or CopilotBillingPolicy.from_environment()
    args = [
        "copilot", "--agent=task-harness-orchestrator", "--model", model,
        "--autopilot",
        f"--max-autopilot-continues={os.getenv('COPILOT_MAX_AUTOPILOT_CONTINUES', '100')}",
    ]
    args.extend(policy.flags())
    if allow_all:
        args.append("--allow-all-tools")
    for denied in (
        "git push", "git merge", "git rebase", "git reset", "git clean",
        "git checkout", "git switch", "rm -rf", "sudo", "su", "chown",
        "chmod -R", "pip install", "uv pip install", "npm install",
    ):
        args.append(f"--deny-tool=shell({denied})")
    return args


