from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class MergeAuthorizationPolicy(str, Enum):
    PER_PR_CONFIRMATION = "per_pr_confirmation"
    STANDING_GATED_AUTHORIZATION = "standing_gated_authorization"


@dataclass(frozen=True, slots=True)
class HostRepositoryPolicy:
    integration_branch: str = "main"
    quality_gate_command: str | None = None
    required_checks: tuple[str, ...] = ()
    completion_labels: tuple[str, ...] = ("done",)
    merge_authorization_policy: MergeAuthorizationPolicy = (
        MergeAuthorizationPolicy.PER_PR_CONFIRMATION
    )


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    model: str | None = None
    reasoning_effort: str | None = None
    max_autopilot_continues: int | None = None


@dataclass(frozen=True, slots=True)
class RepositoryRuntimePolicy:
    model: str | None = None
    reasoning_effort: str | None = None
    max_autopilot_continues: int | None = None


@dataclass(frozen=True, slots=True)
class RuntimeDefaults:
    model: str = "gpt-5.3-codex"
    reasoning_effort: str | None = None
    max_autopilot_continues: int = 100


@dataclass(frozen=True, slots=True)
class ResolvedRuntimeConfig:
    model: str
    reasoning_effort: str | None
    max_autopilot_continues: int
    sources: dict[str, str]


class HostRepositoryPolicyAdapter(Protocol):
    def load_host_policy(self) -> HostRepositoryPolicy: ...

    def load_runtime_policy(self) -> RepositoryRuntimePolicy: ...


def _normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    normalized = value.strip()
    return normalized or None


def _coerce_positive_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def resolve_runtime_config(
    *,
    request: RuntimeRequest | None = None,
    repository_policy: RepositoryRuntimePolicy | None = None,
    environment: Mapping[str, str] | None = None,
    defaults: RuntimeDefaults | None = None,
) -> ResolvedRuntimeConfig:
    """
    Resolve runtime configuration with deterministic precedence:

    request -> repository_policy -> environment -> defaults
    """

    resolved_defaults = defaults or RuntimeDefaults()
    resolved_request = request or RuntimeRequest()
    resolved_repository_policy = repository_policy or RepositoryRuntimePolicy()
    env = environment or {}

    env_model = _normalize_optional_text(env.get("COPILOT_MODEL"))
    env_reasoning_effort = _normalize_optional_text(env.get("COPILOT_REASONING_EFFORT"))
    env_max_autopilot = _coerce_positive_int(env.get("COPILOT_MAX_AUTOPILOT_CONTINUES"))

    model = _normalize_optional_text(resolved_request.model)
    model_source = "request"
    if model is None:
        model = _normalize_optional_text(resolved_repository_policy.model)
        model_source = "repository_policy"
    if model is None:
        model = env_model
        model_source = "environment"
    if model is None:
        model = resolved_defaults.model
        model_source = "defaults"

    reasoning_effort = _normalize_optional_text(resolved_request.reasoning_effort)
    reasoning_source = "request"
    if reasoning_effort is None:
        reasoning_effort = _normalize_optional_text(resolved_repository_policy.reasoning_effort)
        reasoning_source = "repository_policy"
    if reasoning_effort is None:
        reasoning_effort = env_reasoning_effort
        reasoning_source = "environment"
    if reasoning_effort is None:
        reasoning_effort = _normalize_optional_text(resolved_defaults.reasoning_effort)
        reasoning_source = "defaults"

    max_autopilot_continues = _coerce_positive_int(resolved_request.max_autopilot_continues)
    max_autopilot_source = "request"
    if max_autopilot_continues is None:
        max_autopilot_continues = _coerce_positive_int(
            resolved_repository_policy.max_autopilot_continues
        )
        max_autopilot_source = "repository_policy"
    if max_autopilot_continues is None:
        max_autopilot_continues = env_max_autopilot
        max_autopilot_source = "environment"
    if max_autopilot_continues is None:
        max_autopilot_continues = resolved_defaults.max_autopilot_continues
        max_autopilot_source = "defaults"

    return ResolvedRuntimeConfig(
        model=model,
        reasoning_effort=reasoning_effort,
        max_autopilot_continues=max_autopilot_continues,
        sources={
            "model": model_source,
            "reasoning_effort": reasoning_source,
            "max_autopilot_continues": max_autopilot_source,
        },
    )
