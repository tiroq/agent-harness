from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DeliveryRole(str, Enum):
    IMPLEMENTER = "implementer"
    REVIEWER = "reviewer"
    TEST_ENGINEER = "test-engineer"
    EVIDENCE_AUDITOR = "evidence-auditor"
    COMMITTER = "committer"


class DeliveryOutcome(str, Enum):
    FAILED_IMPLEMENTATION = "FAILED_IMPLEMENTATION"
    REWORK = "REWORK"
    BLOCKED_EXTERNAL = "BLOCKED_EXTERNAL"
    BLOCKED_ENVIRONMENT = "BLOCKED_ENVIRONMENT"
    BLOCKED_AUTHORITY = "BLOCKED_AUTHORITY"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"


BLOCKED_OUTCOME_STATES: frozenset[DeliveryOutcome] = frozenset(
    {
        DeliveryOutcome.BLOCKED_EXTERNAL,
        DeliveryOutcome.BLOCKED_ENVIRONMENT,
        DeliveryOutcome.BLOCKED_AUTHORITY,
        DeliveryOutcome.WAITING_EXTERNAL,
    }
)
FAILURE_OUTCOME_STATES: frozenset[DeliveryOutcome] = frozenset(
    {
        DeliveryOutcome.FAILED_IMPLEMENTATION,
        DeliveryOutcome.REWORK,
    }
)


def is_blocked_outcome(outcome: DeliveryOutcome) -> bool:
    return outcome in BLOCKED_OUTCOME_STATES


def is_failure_outcome(outcome: DeliveryOutcome) -> bool:
    return outcome in FAILURE_OUTCOME_STATES


class PrerequisiteStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RoleReport:
    role: str
    generated_by_role: str
    agent_session_id: str
    content: str = ""


@dataclass(frozen=True, slots=True)
class ProvenanceValidation:
    accepted: bool
    errors: tuple[str, ...]
    expected_role: DeliveryRole
    expected_agent_session_id: str


@dataclass(frozen=True, slots=True)
class PrerequisiteCheck:
    name: str
    status: PrerequisiteStatus
    mandatory: bool = True
    on_failure: DeliveryOutcome = DeliveryOutcome.BLOCKED_EXTERNAL


@dataclass(frozen=True, slots=True)
class PreAuditDecision:
    should_invoke_auditor: bool
    failed_prerequisites: tuple[str, ...]
    blocking_states: tuple[DeliveryOutcome, ...]
    failure_states: tuple[DeliveryOutcome, ...]
    reason: str
