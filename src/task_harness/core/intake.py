from __future__ import annotations

from dataclasses import dataclass

from .models import DeliveryRole, ProvenanceValidation, RoleReport


def _coerce_role(raw: str) -> DeliveryRole | None:
    normalized = raw.strip().lower().replace("_", "-")
    for candidate in DeliveryRole:
        if candidate.value == normalized:
            return candidate
    return None


def validate_role_report_provenance(
    expected_role: DeliveryRole, expected_agent_session_id: str, report: RoleReport
) -> ProvenanceValidation:
    errors: list[str] = []

    report_role = _coerce_role(report.role)
    if report_role is None:
        errors.append(f"unknown ROLE value '{report.role}'")
    elif report_role != expected_role:
        errors.append(f"ROLE mismatch: expected {expected_role.value}, got {report_role.value}")

    generated_by_role = _coerce_role(report.generated_by_role)
    if generated_by_role is None:
        errors.append(f"unknown GENERATED_BY_ROLE value '{report.generated_by_role}'")
    elif generated_by_role != expected_role:
        errors.append(
            f"GENERATED_BY_ROLE mismatch: expected {expected_role.value}, got {generated_by_role.value}"
        )

    if report.agent_session_id != expected_agent_session_id:
        errors.append(
            "AGENT_SESSION_ID mismatch: "
            f"expected {expected_agent_session_id}, got {report.agent_session_id}"
        )

    return ProvenanceValidation(
        accepted=not errors,
        errors=tuple(errors),
        expected_role=expected_role,
        expected_agent_session_id=expected_agent_session_id,
    )


@dataclass(frozen=True, slots=True)
class RoleReportIntakeResult:
    accepted: bool
    validation: ProvenanceValidation
    report: RoleReport | None


def intake_role_report(
    expected_role: DeliveryRole, expected_agent_session_id: str, report: RoleReport
) -> RoleReportIntakeResult:
    validation = validate_role_report_provenance(expected_role, expected_agent_session_id, report)
    return RoleReportIntakeResult(
        accepted=validation.accepted,
        validation=validation,
        report=report if validation.accepted else None,
    )
