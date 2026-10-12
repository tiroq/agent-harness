"""Typed delivery orchestration primitives shared by CLI and MCP adapters."""

from .environment import NormalizedEnvironment, normalize_environment
from .gates import decide_pre_audit_gate
from .intake import RoleReportIntakeResult, intake_role_report, validate_role_report_provenance
from .models import (
    BLOCKED_OUTCOME_STATES,
    FAILURE_OUTCOME_STATES,
    DeliveryOutcome,
    DeliveryRole,
    PreAuditDecision,
    PrerequisiteCheck,
    PrerequisiteStatus,
    ProvenanceValidation,
    RoleReport,
    is_blocked_outcome,
    is_failure_outcome,
)
from .usage import RoleUsageTracker

__all__ = [
    "BLOCKED_OUTCOME_STATES",
    "FAILURE_OUTCOME_STATES",
    "DeliveryOutcome",
    "DeliveryRole",
    "NormalizedEnvironment",
    "PreAuditDecision",
    "PrerequisiteCheck",
    "PrerequisiteStatus",
    "ProvenanceValidation",
    "RoleReport",
    "RoleReportIntakeResult",
    "RoleUsageTracker",
    "decide_pre_audit_gate",
    "intake_role_report",
    "is_blocked_outcome",
    "is_failure_outcome",
    "normalize_environment",
    "validate_role_report_provenance",
]
