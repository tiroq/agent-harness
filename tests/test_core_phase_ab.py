from pathlib import Path

import pytest

from task_harness.core import (
    DeliveryOutcome,
    DeliveryPhase,
    HostRepositoryPolicy,
    IssueComment,
    IssueSnapshot,
    RefreshVerdict,
    RepositoryRuntimePolicy,
    RuntimeDefaults,
    RuntimeRequest,
    WorktreeOwnership,
    advance_phase,
    classify_worktree_ownership,
    issue_number_from_branch_name,
    issue_number_from_worktree_path,
    materialize_issue_packet,
    parse_refresh_evidence,
    parse_repository_slug,
    resolve_runtime_config,
    resume_from_blocked,
    transition_with_outcome,
)


def test_branch_and_worktree_issue_number_parsing() -> None:
    assert issue_number_from_branch_name("feat/76-refactor-workflow") == 76
    assert issue_number_from_branch_name("issue-12") == 12
    assert issue_number_from_branch_name("main") is None

    assert issue_number_from_worktree_path(Path("/repo/.worktree/issue-89")) == 89
    assert issue_number_from_worktree_path(Path("/repo/.worktree/sandbox")) is None


def test_repository_slug_parsing_accepts_github_ssh_and_https() -> None:
    assert parse_repository_slug("git@github.com:tiroq/agent-harness.git") == (
        "tiroq",
        "agent-harness",
    )
    assert parse_repository_slug("https://github.com/tiroq/agent-harness") == (
        "tiroq",
        "agent-harness",
    )
    assert parse_repository_slug("ssh://git.example.com/private/repo") is None


def test_worktree_ownership_classification() -> None:
    repo_root = Path("/tmp/repo")

    owned = classify_worktree_ownership(
        repo_root=repo_root,
        worktree_path=repo_root / ".worktree" / "issue-76",
        expected_issue_number=76,
        branch_name="refactor/76-refactor-workflow-migrate-brandforge-deliv",
    )
    assert owned is WorktreeOwnership.OWNED

    foreign_issue = classify_worktree_ownership(
        repo_root=repo_root,
        worktree_path=repo_root / ".worktree" / "issue-77",
        expected_issue_number=76,
        branch_name="refactor/77-another-issue",
    )
    assert foreign_issue is WorktreeOwnership.FOREIGN_ISSUE

    foreign_branch = classify_worktree_ownership(
        repo_root=repo_root,
        worktree_path=repo_root / ".worktree" / "scratchpad",
        expected_issue_number=76,
        branch_name="main",
    )
    assert foreign_branch is WorktreeOwnership.FOREIGN_BRANCH

    outside = classify_worktree_ownership(
        repo_root=repo_root,
        worktree_path=Path("/outside/issue-76"),
        expected_issue_number=76,
        branch_name="refactor/76-refactor-workflow-migrate-brandforge-deliv",
    )
    assert outside is WorktreeOwnership.OUTSIDE_REPOSITORY


def test_lifecycle_linear_transition_and_optional_final_review() -> None:
    phase = DeliveryPhase.READY
    expected_flow = (
        DeliveryPhase.IMPLEMENTING,
        DeliveryPhase.FOCUSED_TESTS,
        DeliveryPhase.PONYTAIL,
        DeliveryPhase.REVIEW,
        DeliveryPhase.INDEPENDENT_TESTING,
    )
    for expected_phase in expected_flow:
        phase = advance_phase(phase)
        assert phase is expected_phase

    assert advance_phase(phase, include_final_review=True) is DeliveryPhase.FINAL_REVIEW
    assert advance_phase(DeliveryPhase.FINAL_REVIEW) is DeliveryPhase.PRE_COMMIT_AUDIT


def test_lifecycle_outcome_transitions_cover_rework_blocked_and_failed() -> None:
    assert (
        transition_with_outcome(DeliveryPhase.REVIEW, DeliveryOutcome.REWORK)
        is DeliveryPhase.IMPLEMENTING
    )
    assert (
        transition_with_outcome(
            DeliveryPhase.PRE_COMMIT_AUDIT, DeliveryOutcome.BLOCKED_ENVIRONMENT
        )
        is DeliveryPhase.BLOCKED
    )
    assert (
        transition_with_outcome(
            DeliveryPhase.IMPLEMENTING, DeliveryOutcome.FAILED_IMPLEMENTATION
        )
        is DeliveryPhase.FAILED
    )
    assert resume_from_blocked(DeliveryPhase.PRE_COMMIT_AUDIT) is DeliveryPhase.PRE_COMMIT_AUDIT

    with pytest.raises(ValueError, match="REWORK cannot be applied"):
        transition_with_outcome(DeliveryPhase.COMMIT, DeliveryOutcome.REWORK)


def test_runtime_config_precedence_is_request_then_repo_then_env_then_defaults() -> None:
    resolved = resolve_runtime_config(
        request=RuntimeRequest(model="gpt-5.5"),
        repository_policy=RepositoryRuntimePolicy(
            model="gpt-5.4",
            reasoning_effort="high",
            max_autopilot_continues=120,
        ),
        environment={
            "COPILOT_MODEL": "gpt-5.3-codex",
            "COPILOT_REASONING_EFFORT": "medium",
            "COPILOT_MAX_AUTOPILOT_CONTINUES": "90",
        },
        defaults=RuntimeDefaults(model="gpt-5-mini", max_autopilot_continues=50),
    )

    assert resolved.model == "gpt-5.5"
    assert resolved.reasoning_effort == "high"
    assert resolved.max_autopilot_continues == 120
    assert resolved.sources == {
        "model": "request",
        "reasoning_effort": "repository_policy",
        "max_autopilot_continues": "repository_policy",
    }


def test_runtime_config_falls_back_to_defaults_for_invalid_environment_values() -> None:
    resolved = resolve_runtime_config(
        repository_policy=RepositoryRuntimePolicy(),
        environment={"COPILOT_MAX_AUTOPILOT_CONTINUES": "invalid"},
        defaults=RuntimeDefaults(max_autopilot_continues=42),
    )

    assert resolved.max_autopilot_continues == 42
    assert resolved.sources["max_autopilot_continues"] == "defaults"


def test_host_repository_policy_defaults_are_safe() -> None:
    policy = HostRepositoryPolicy()
    assert policy.integration_branch == "main"
    assert policy.completion_labels == ("done",)
    assert policy.required_checks == ()


def test_issue_packet_materialization_prioritizes_latest_amendments() -> None:
    comments = (
        IssueComment(
            id=101,
            author="alice",
            body="Old amendment",
            created_at="2026-10-12T01:00:00Z",
            updated_at="2026-10-12T01:00:00Z",
            url="https://example.test/comments/101",
        ),
        IssueComment(
            id=102,
            author="bob",
            body="Newest amendment",
            created_at="2026-10-12T02:00:00Z",
            updated_at="2026-10-12T02:05:00Z",
            url="https://example.test/comments/102",
        ),
    )
    snapshot = IssueSnapshot(
        number=76,
        url="https://github.com/tiroq/brandforge/issues/76",
        title="refactor(workflow): migrate delivery engine",
        body="Line A\nLine B\nLine C",
        comments=comments,
    )

    packet = materialize_issue_packet(
        snapshot,
        issue_comments_file="/tmp/issue-76-comments.md",
        latest_comment_limit=2,
        body_tail_lines=2,
    )

    assert packet.comments_count == 2
    assert packet.latest_comment_id == 102
    assert packet.latest_comment_updated_at == "2026-10-12T02:05:00Z"
    assert "## Current authoritative state / latest amendments" in packet.markdown
    assert "#### Latest comment 102" in packet.markdown
    assert packet.markdown.index("#### Latest comment 102") < packet.markdown.index(
        "#### Latest comment 101"
    )
    assert "## Canonical issue body (baseline)" in packet.markdown
    assert "Line B\nLine C" in packet.markdown


def test_refresh_evidence_parser_maps_structured_output_to_typed_fields() -> None:
    parsed = parse_refresh_evidence(
        "\n".join(
            [
                "VERDICT: REBASED",
                "BASE_FRESHNESS: stale",
                "REBASE_PERFORMED: YES",
                "MERGE_PERFORMED: NO",
                "PATCH_EQUIVALENCE: PRESERVED",
                "ISSUE_HEAD_SHA_BEFORE: 1111",
                "ISSUE_HEAD_SHA_AFTER: 2222",
                "INTEGRATION_HEAD_SHA: aaaa",
                "REMOTE_ISSUE_BRANCH_SHA: bbbb",
                "WORKTREE_RECOVERED: YES",
                "CONFLICT_PATHS: src/a.py, src/b.py",
            ]
        )
    )

    assert parsed.verdict is RefreshVerdict.REBASED
    assert parsed.base_freshness == "stale"
    assert parsed.rebase_performed is True
    assert parsed.merge_performed is False
    assert parsed.patch_equivalence == "PRESERVED"
    assert parsed.issue_head_sha_before == "1111"
    assert parsed.issue_head_sha_after == "2222"
    assert parsed.integration_head_sha == "aaaa"
    assert parsed.remote_issue_branch_sha == "bbbb"
    assert parsed.worktree_recovered == "YES"
    assert parsed.conflict_paths == ("src/a.py", "src/b.py")


def test_refresh_evidence_parser_requires_verdict() -> None:
    with pytest.raises(ValueError, match="missing VERDICT"):
        parse_refresh_evidence("BASE_FRESHNESS: current")
