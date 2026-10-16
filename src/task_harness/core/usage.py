from __future__ import annotations

from dataclasses import dataclass, field

from .models import DeliveryRole


@dataclass(slots=True)
class RoleUsageTracker:
    _counts: dict[DeliveryRole, int] = field(default_factory=dict)

    def record_invocation(self, role: DeliveryRole) -> int:
        current = self._counts.get(role, 0)
        updated = current + 1
        self._counts[role] = updated
        return updated

    def count(self, role: DeliveryRole) -> int:
        return self._counts.get(role, 0)

    @property
    def total_invocations(self) -> int:
        return sum(self._counts.values())

    def as_dict(self) -> dict[str, int]:
        return {role.value: self._counts.get(role, 0) for role in DeliveryRole}
