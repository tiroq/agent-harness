from __future__ import annotations

from enum import Enum

from .models import DeliveryOutcome, is_blocked_outcome


class DeliveryPhase(str, Enum):
    READY = "READY"
    IMPLEMENTING = "IMPLEMENTING"
    FOCUSED_TESTS = "FOCUSED_TESTS"
    PONYTAIL = "PONYTAIL"
    REVIEW = "REVIEW"
    INDEPENDENT_TESTING = "INDEPENDENT_TESTING"
    FINAL_REVIEW = "FINAL_REVIEW"
    PRE_COMMIT_AUDIT = "PRE_COMMIT_AUDIT"
    COMMIT = "COMMIT"
    INTEGRATION_REFRESH = "INTEGRATION_REFRESH"
    POST_REFRESH_VALIDATION = "POST_REFRESH_VALIDATION"
    POST_COMMIT_AUDIT = "POST_COMMIT_AUDIT"
    PUBLISH = "PUBLISH"
    REMOTE_PR_VALIDATION = "REMOTE_PR_VALIDATION"
    PR_READY = "PR_READY"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


_DEFAULT_PHASE_SEQUENCE = (
    DeliveryPhase.READY,
    DeliveryPhase.IMPLEMENTING,
    DeliveryPhase.FOCUSED_TESTS,
    DeliveryPhase.PONYTAIL,
    DeliveryPhase.REVIEW,
    DeliveryPhase.INDEPENDENT_TESTING,
    DeliveryPhase.PRE_COMMIT_AUDIT,
    DeliveryPhase.COMMIT,
    DeliveryPhase.INTEGRATION_REFRESH,
    DeliveryPhase.POST_REFRESH_VALIDATION,
    DeliveryPhase.POST_COMMIT_AUDIT,
    DeliveryPhase.PUBLISH,
    DeliveryPhase.REMOTE_PR_VALIDATION,
    DeliveryPhase.PR_READY,
)
_NEXT_PHASE = {
    current: _DEFAULT_PHASE_SEQUENCE[index + 1]
    for index, current in enumerate(_DEFAULT_PHASE_SEQUENCE[:-1])
}
_REWORK_SOURCE_PHASES = frozenset(
    {
        DeliveryPhase.REVIEW,
        DeliveryPhase.INDEPENDENT_TESTING,
        DeliveryPhase.FINAL_REVIEW,
        DeliveryPhase.PRE_COMMIT_AUDIT,
        DeliveryPhase.POST_COMMIT_AUDIT,
        DeliveryPhase.REMOTE_PR_VALIDATION,
    }
)


def phase_sequence() -> tuple[DeliveryPhase, ...]:
    return _DEFAULT_PHASE_SEQUENCE


def advance_phase(
    current: DeliveryPhase,
    *,
    include_final_review: bool = False,
) -> DeliveryPhase:
    """Advance one lifecycle step, honoring optional final review hops."""

    if current in {DeliveryPhase.BLOCKED, DeliveryPhase.FAILED, DeliveryPhase.PR_READY}:
        msg = f"cannot advance terminal phase {current.value}"
        raise ValueError(msg)

    if current == DeliveryPhase.INDEPENDENT_TESTING and include_final_review:
        return DeliveryPhase.FINAL_REVIEW
    if current == DeliveryPhase.FINAL_REVIEW:
        return DeliveryPhase.PRE_COMMIT_AUDIT

    next_phase = _NEXT_PHASE.get(current)
    if next_phase is None:
        msg = f"unsupported phase transition from {current.value}"
        raise ValueError(msg)
    return next_phase


def transition_with_outcome(
    current: DeliveryPhase,
    outcome: DeliveryOutcome | None = None,
    *,
    include_final_review: bool = False,
) -> DeliveryPhase:
    """Apply deterministic outcome-aware lifecycle transition rules."""

    if outcome is None:
        return advance_phase(current, include_final_review=include_final_review)

    if outcome is DeliveryOutcome.REWORK:
        if current not in _REWORK_SOURCE_PHASES:
            msg = f"REWORK cannot be applied from phase {current.value}"
            raise ValueError(msg)
        return DeliveryPhase.IMPLEMENTING

    if outcome is DeliveryOutcome.FAILED_IMPLEMENTATION:
        return DeliveryPhase.FAILED

    if is_blocked_outcome(outcome):
        return DeliveryPhase.BLOCKED

    msg = f"unsupported outcome transition: {outcome.value}"
    raise ValueError(msg)


def resume_from_blocked(previous_phase: DeliveryPhase) -> DeliveryPhase:
    """Resume a blocked run from a prior non-terminal lifecycle phase."""

    if previous_phase in {DeliveryPhase.BLOCKED, DeliveryPhase.FAILED, DeliveryPhase.PR_READY}:
        msg = f"cannot resume blocked run into terminal phase {previous_phase.value}"
        raise ValueError(msg)
    return previous_phase
