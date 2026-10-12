from task_harness.core import (
    DeliveryOutcome,
    DeliveryRole,
    PrerequisiteCheck,
    PrerequisiteStatus,
    RoleReport,
    RoleUsageTracker,
    decide_pre_audit_gate,
    intake_role_report,
    is_blocked_outcome,
    is_failure_outcome,
    normalize_environment,
)


def test_role_report_intake_accepts_matching_provenance() -> None:
    report = RoleReport(
        role="reviewer",
        generated_by_role="reviewer",
        agent_session_id="reviewer-session-123",
        content="report body",
    )

    result = intake_role_report(DeliveryRole.REVIEWER, "reviewer-session-123", report)

    assert result.accepted is True
    assert result.report == report
    assert result.validation.errors == ()


def test_role_report_intake_rejects_provenance_mismatch() -> None:
    report = RoleReport(
        role="implementer",
        generated_by_role="reviewer",
        agent_session_id="wrong-session",
        content="report body",
    )

    result = intake_role_report(DeliveryRole.REVIEWER, "reviewer-session-123", report)

    assert result.accepted is False
    assert result.report is None
    assert result.validation.errors == (
        "ROLE mismatch: expected reviewer, got implementer",
        "AGENT_SESSION_ID mismatch: expected reviewer-session-123, got wrong-session",
    )


def test_role_report_intake_rejects_unknown_role_tokens() -> None:
    report = RoleReport(
        role="chief-reviewer",
        generated_by_role="qa",
        agent_session_id="reviewer-session-123",
        content="report body",
    )

    result = intake_role_report(DeliveryRole.REVIEWER, "reviewer-session-123", report)

    assert result.accepted is False
    assert result.report is None
    assert result.validation.errors == (
        "unknown ROLE value 'chief-reviewer'",
        "unknown GENERATED_BY_ROLE value 'qa'",
    )


def test_role_report_intake_accepts_case_and_underscore_role_tokens() -> None:
    report = RoleReport(
        role=" Test_Engineer ",
        generated_by_role="test-engineer",
        agent_session_id="test-session-42",
        content="report body",
    )

    result = intake_role_report(DeliveryRole.TEST_ENGINEER, "test-session-42", report)

    assert result.accepted is True
    assert result.report == report
    assert result.validation.errors == ()


def test_pre_audit_gate_blocks_auditor_for_known_red_mandatory_prerequisite() -> None:
    decision = decide_pre_audit_gate(
        [
            PrerequisiteCheck(
                name="global-quality-gate",
                status=PrerequisiteStatus.FAIL,
                mandatory=True,
                on_failure=DeliveryOutcome.BLOCKED_ENVIRONMENT,
            ),
            PrerequisiteCheck(
                name="review-report",
                status=PrerequisiteStatus.PASS,
                mandatory=True,
                on_failure=DeliveryOutcome.REWORK,
            ),
        ]
    )

    assert decision.should_invoke_auditor is False
    assert decision.failed_prerequisites == ("global-quality-gate",)
    assert decision.blocking_states == (DeliveryOutcome.BLOCKED_ENVIRONMENT,)
    assert decision.failure_states == ()


def test_pre_audit_gate_sorts_multiple_known_red_prerequisites_deterministically() -> None:
    decision = decide_pre_audit_gate(
        [
            PrerequisiteCheck(
                name="review-report",
                status=PrerequisiteStatus.FAIL,
                mandatory=True,
                on_failure=DeliveryOutcome.REWORK,
            ),
            PrerequisiteCheck(
                name="quality-gate",
                status=PrerequisiteStatus.FAIL,
                mandatory=True,
                on_failure=DeliveryOutcome.BLOCKED_ENVIRONMENT,
            ),
            PrerequisiteCheck(
                name="branch-safety",
                status=PrerequisiteStatus.FAIL,
                mandatory=True,
                on_failure=DeliveryOutcome.BLOCKED_ENVIRONMENT,
            ),
            PrerequisiteCheck(
                name="artifact-presence",
                status=PrerequisiteStatus.FAIL,
                mandatory=True,
                on_failure=DeliveryOutcome.BLOCKED_EXTERNAL,
            ),
        ]
    )

    assert decision.should_invoke_auditor is False
    assert decision.failed_prerequisites == (
        "artifact-presence",
        "branch-safety",
        "quality-gate",
        "review-report",
    )
    assert decision.blocking_states == (
        DeliveryOutcome.BLOCKED_EXTERNAL,
        DeliveryOutcome.BLOCKED_ENVIRONMENT,
    )
    assert decision.failure_states == (DeliveryOutcome.REWORK,)
    assert (
        decision.reason
        == "mandatory known-red prerequisites failed: artifact-presence, branch-safety, quality-gate, review-report"
    )


def test_pre_audit_gate_allows_auditor_without_known_red_mandatory_prerequisites() -> None:
    decision = decide_pre_audit_gate(
        [
            PrerequisiteCheck(
                name="global-quality-gate",
                status=PrerequisiteStatus.UNKNOWN,
                mandatory=True,
                on_failure=DeliveryOutcome.BLOCKED_ENVIRONMENT,
            ),
            PrerequisiteCheck(
                name="lint-warnings",
                status=PrerequisiteStatus.FAIL,
                mandatory=False,
                on_failure=DeliveryOutcome.REWORK,
            ),
        ]
    )

    assert decision.should_invoke_auditor is True
    assert decision.failed_prerequisites == ()


def test_normalize_environment_removes_internal_sentinels_and_has_stable_fingerprint() -> None:
    environment_one = {
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin",
        "TASK_HARNESS_RUN_ID": "run-123",
        "COPILOT_AUTOPILOT_AUTHORITATIVE_RUNNER": "1",
    }
    environment_two = {
        "COPILOT_AUTOPILOT_AUTHORITATIVE_RUNNER": "1",
        "TASK_HARNESS_RUN_ID": "run-123",
        "PATH": "/usr/local/bin:/usr/bin",
        "LANG": "C.UTF-8",
    }

    normalized_one = normalize_environment(environment_one)
    normalized_two = normalize_environment(environment_two)

    assert normalized_one.values == {
        "LANG": "C.UTF-8",
        "PATH": "/usr/local/bin:/usr/bin",
    }
    assert normalized_one.fingerprint == normalized_two.fingerprint


def test_normalize_environment_applies_allowlist_drop_keys_and_prefixes() -> None:
    environment = {
        "PATH": "/usr/local/bin:/usr/bin",
        "KEEP": "yes",
        "DROP": "remove",
        "RETRIES": 3,
        "TASK_HARNESS_INTERNAL_DEBUG": "1",
    }

    normalized = normalize_environment(
        environment,
        allowlist=["RETRIES", "DROP", "KEEP", "PATH", "TASK_HARNESS_INTERNAL_DEBUG"],
        drop_keys=("DROP",),
    )
    reordered_allowlist = normalize_environment(
        environment,
        allowlist=["TASK_HARNESS_INTERNAL_DEBUG", "PATH", "KEEP", "DROP", "RETRIES"],
        drop_keys=("DROP",),
    )

    assert normalized.values == {
        "KEEP": "yes",
        "PATH": "/usr/local/bin:/usr/bin",
        "RETRIES": "3",
    }
    assert normalized.fingerprint == reordered_allowlist.fingerprint


def test_outcome_taxonomy_distinguishes_blocked_and_failure_states() -> None:
    assert is_blocked_outcome(DeliveryOutcome.BLOCKED_AUTHORITY) is True
    assert is_blocked_outcome(DeliveryOutcome.WAITING_EXTERNAL) is True
    assert is_blocked_outcome(DeliveryOutcome.FAILED_IMPLEMENTATION) is False

    assert is_failure_outcome(DeliveryOutcome.FAILED_IMPLEMENTATION) is True
    assert is_failure_outcome(DeliveryOutcome.REWORK) is True
    assert is_failure_outcome(DeliveryOutcome.BLOCKED_EXTERNAL) is False


def test_role_usage_tracker_counts_invocations_per_role() -> None:
    tracker = RoleUsageTracker()

    assert tracker.count(DeliveryRole.EVIDENCE_AUDITOR) == 0
    assert tracker.record_invocation(DeliveryRole.EVIDENCE_AUDITOR) == 1
    assert tracker.record_invocation(DeliveryRole.EVIDENCE_AUDITOR) == 2
    assert tracker.record_invocation(DeliveryRole.REVIEWER) == 1
    assert tracker.total_invocations == 3
    assert tracker.as_dict()["evidence-auditor"] == 2
    assert tracker.as_dict()["reviewer"] == 1


def test_role_usage_tracker_as_dict_includes_zeroes_for_unseen_roles() -> None:
    tracker = RoleUsageTracker()
    tracker.record_invocation(DeliveryRole.COMMITTER)

    counts = tracker.as_dict()

    assert counts["committer"] == 1
    assert counts["implementer"] == 0
    assert counts["reviewer"] == 0
    assert counts["test-engineer"] == 0
    assert counts["evidence-auditor"] == 0
    assert sum(counts.values()) == tracker.total_invocations
