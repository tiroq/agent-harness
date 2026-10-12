from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

DEFAULT_ENV_DROP_KEYS: frozenset[str] = frozenset(
    {
        "COPILOT_AUTOPILOT_AUTHORITATIVE_RUNNER",
        "TASK_HARNESS_RUN_ID",
        "TASK_HARNESS_ROLE",
        "TASK_HARNESS_PHASE",
    }
)
DEFAULT_ENV_DROP_PREFIXES: tuple[str, ...] = (
    "COPILOT_AUTOPILOT_",
    "TASK_HARNESS_RUN_",
    "TASK_HARNESS_INTERNAL_",
)


@dataclass(frozen=True, slots=True)
class NormalizedEnvironment:
    values: dict[str, str]
    fingerprint: str


def normalize_environment(
    environment: Mapping[str, str],
    *,
    allowlist: Iterable[str] | None = None,
    drop_keys: Iterable[str] = (),
    drop_prefixes: Iterable[str] = (),
) -> NormalizedEnvironment:
    denied_keys = set(DEFAULT_ENV_DROP_KEYS)
    denied_keys.update(drop_keys)
    denied_prefixes = DEFAULT_ENV_DROP_PREFIXES + tuple(drop_prefixes)
    selected_keys = (
        sorted(key for key in allowlist if key in environment)
        if allowlist is not None
        else sorted(environment.keys())
    )

    normalized: dict[str, str] = {}
    for key in selected_keys:
        if key in denied_keys or any(key.startswith(prefix) for prefix in denied_prefixes):
            continue
        normalized[key] = str(environment[key])

    serialized = "\n".join(f"{key}={normalized[key]}" for key in sorted(normalized))
    fingerprint = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return NormalizedEnvironment(values=normalized, fingerprint=fingerprint)
