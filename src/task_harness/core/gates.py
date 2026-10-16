from __future__ import annotations

from collections.abc import Sequence

from .models import (
    DeliveryOutcome,
    PreAuditDecision,
    PrerequisiteCheck,
    PrerequisiteStatus,
    is_blocked_outcome,
    is_failure_outcome,
)


def decide_pre_audit_gate(checks: Sequence[PrerequisiteCheck]) -> PreAuditDecision:
    failed_mandatory_checks = tuple(
        sorted(
            (
                check
                for check in checks
                if check.mandatory and check.status == PrerequisiteStatus.FAIL
            ),
            key=lambda check: check.name,
        )
    )
    if not failed_mandatory_checks:
        return PreAuditDecision(
            should_invoke_auditor=True,
            failed_prerequisites=(),
            blocking_states=(),
            failure_states=(),
            reason="no mandatory known-red prerequisite is present",
        )

    blocking_states: list[DeliveryOutcome] = []
    failure_states: list[DeliveryOutcome] = []
    for check in failed_mandatory_checks:
        if is_blocked_outcome(check.on_failure) and check.on_failure not in blocking_states:
            blocking_states.append(check.on_failure)
        elif is_failure_outcome(check.on_failure) and check.on_failure not in failure_states:
            failure_states.append(check.on_failure)

    failed_names = tuple(check.name for check in failed_mandatory_checks)
    return PreAuditDecision(
        should_invoke_auditor=False,
        failed_prerequisites=failed_names,
        blocking_states=tuple(blocking_states),
        failure_states=tuple(failure_states),
        reason=f"mandatory known-red prerequisites failed: {', '.join(failed_names)}",
    )
