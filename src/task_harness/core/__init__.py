"""Typed delivery orchestration primitives shared by CLI and MCP adapters."""

from .config import (
    HostRepositoryPolicy,
    HostRepositoryPolicyAdapter,
    MergeAuthorizationPolicy,
    RepositoryRuntimePolicy,
    ResolvedRuntimeConfig,
    RuntimeDefaults,
    RuntimeRequest,
    resolve_runtime_config,
)
from .environment import NormalizedEnvironment, normalize_environment
from .gates import decide_pre_audit_gate
from .identity import (
    IssueBranch,
    IssueIdentity,
    IssueWorktree,
    RepositoryIdentity,
    WorktreeOwnership,
    classify_worktree_ownership,
    issue_number_from_branch_name,
    issue_number_from_worktree_path,
    parse_repository_slug,
)
from .intake import RoleReportIntakeResult, intake_role_report, validate_role_report_provenance
from .lifecycle import (
    DeliveryPhase,
    advance_phase,
    resume_from_blocked,
    transition_with_outcome,
)
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
from .packet import IssueComment, IssueSnapshot, MaterializedIssuePacket, materialize_issue_packet
from .refresh import RefreshEvidence, RefreshVerdict, parse_refresh_evidence
from .usage import RoleUsageTracker

__all__ = [
    "BLOCKED_OUTCOME_STATES",
    "DeliveryPhase",
    "FAILURE_OUTCOME_STATES",
    "DeliveryOutcome",
    "DeliveryRole",
    "HostRepositoryPolicy",
    "HostRepositoryPolicyAdapter",
    "IssueBranch",
    "IssueComment",
    "IssueIdentity",
    "IssueSnapshot",
    "IssueWorktree",
    "MaterializedIssuePacket",
    "MergeAuthorizationPolicy",
    "NormalizedEnvironment",
    "PreAuditDecision",
    "PrerequisiteCheck",
    "PrerequisiteStatus",
    "ProvenanceValidation",
    "RefreshEvidence",
    "RefreshVerdict",
    "RepositoryIdentity",
    "RepositoryRuntimePolicy",
    "ResolvedRuntimeConfig",
    "RoleReport",
    "RoleReportIntakeResult",
    "RoleUsageTracker",
    "RuntimeDefaults",
    "RuntimeRequest",
    "WorktreeOwnership",
    "advance_phase",
    "classify_worktree_ownership",
    "decide_pre_audit_gate",
    "intake_role_report",
    "is_blocked_outcome",
    "is_failure_outcome",
    "issue_number_from_branch_name",
    "issue_number_from_worktree_path",
    "materialize_issue_packet",
    "normalize_environment",
    "parse_refresh_evidence",
    "parse_repository_slug",
    "resolve_runtime_config",
    "resume_from_blocked",
    "transition_with_outcome",
    "validate_role_report_provenance",
]
