"""Two independent GitHub Copilot billing modes; never equate role counts with cost."""

from __future__ import annotations

import pytest

from task_harness.core.copilot_cli import (
    CopilotBillingPolicy, copilot_arguments,
)


def test_default_preserves_existing_arguments_without_claiming_billing():
    policy = CopilotBillingPolicy.from_environment({})
    assert policy.mode == "unconfigured"
    assert policy.max_ai_credits is None
    assert policy.as_dict() == {
        "mode": "unconfigured",
        "unit": "UNKNOWN",
        "max_ai_credits_soft_limit": None,
        "provider_usage_verified": False,
    }
    args = copilot_arguments(
        model="gpt-5.3-codex", allow_all=False, billing_policy=policy,
    )
    assert not any(x.startswith("--max-ai-credits") for x in args)


def test_legacy_premium_requests_do_not_get_fake_ai_credit_cap():
    policy = CopilotBillingPolicy.from_environment({
        "COPILOT_BILLING_MODE": "legacy_premium_requests",
    })
    assert policy.mode == "legacy_premium_requests"
    assert policy.as_dict()["unit"] == "PREMIUM_REQUESTS"
    assert policy.as_dict()["provider_usage_verified"] is False
    args = copilot_arguments(
        model="gpt-5.3-codex", allow_all=True, billing_policy=policy,
    )
    assert not any(x.startswith("--max-ai-credits") for x in args)
    assert "--allow-all-tools" in args


def test_new_ai_credit_mode_adds_only_documented_soft_limit():
    defaults = CopilotBillingPolicy.from_environment({
        "COPILOT_BILLING_MODE": "ai_credits",
    })
    assert defaults.flags() == ["--max-ai-credits=30"]
    explicit = CopilotBillingPolicy.from_environment({
        "COPILOT_BILLING_MODE": "ai_credits",
        "COPILOT_MAX_AI_CREDITS": "90",
    })
    assert explicit.flags() == ["--max-ai-credits=90"]
    assert explicit.as_dict()["unit"] == "AI_CREDITS"
    assert explicit.as_dict()["provider_usage_verified"] is False
    args = copilot_arguments(
        model="gpt-5.3-codex", allow_all=False,
        billing_policy=explicit,
    )
    assert "--max-ai-credits=90" in args


@pytest.mark.parametrize("mode", ["auto", "legacy", "credits", "premium_requests", "unknown"])
def test_unverified_account_auto_detection_never_guessed(mode):
    with pytest.raises(ValueError, match="COPILOT_BILLING_MODE"):
        CopilotBillingPolicy.from_environment({"COPILOT_BILLING_MODE": mode})


@pytest.mark.parametrize("value", ["0", "2", "29", "-1", "x30", "30.5", "1000001", "１２３"])
def test_invalid_ai_credit_limits_rejected_before_launch(value):
    with pytest.raises(ValueError, match="COPILOT_MAX_AI_CREDITS"):
        CopilotBillingPolicy.from_environment({
            "COPILOT_BILLING_MODE": "ai_credits",
            "COPILOT_MAX_AI_CREDITS": value,
        })


@pytest.mark.parametrize("mode", ["legacy_premium_requests", "unconfigured"])
def test_ai_credit_limit_cannot_be_accidentally_applied_to_other_units(mode):
    with pytest.raises(ValueError, match="only valid"):
        CopilotBillingPolicy.from_environment({
            "COPILOT_BILLING_MODE": mode,
            "COPILOT_MAX_AI_CREDITS": "30",
        })


def test_explicit_billing_mode_is_never_derived_from_model_or_calendar():
    # Same model and same date are not evidence of account subscription.
    legacy = CopilotBillingPolicy.from_environment({
        "COPILOT_BILLING_MODE": "legacy_premium_requests",
    })
    credits = CopilotBillingPolicy.from_environment({
        "COPILOT_BILLING_MODE": "ai_credits",
    })
    assert legacy.flags() == []
    assert credits.flags() == ["--max-ai-credits=30"]
    assert legacy.as_dict()["unit"] != credits.as_dict()["unit"]
