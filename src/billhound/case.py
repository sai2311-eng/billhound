"""The working file for one bill as it moves through the graph.

Agents write findings here through tools; edge conditions read it. Routing
never depends on parsing an agent's prose, so a chatty model cannot talk the
pipeline into submitting something.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .schemas import (
    AuditReport,
    Bill,
    Draft,
    Escalation,
    RateCard,
    Recommendation,
    Severity,
)


@dataclass
class Case:
    source: str
    raw_text: str
    history: list[Bill] = field(default_factory=list)
    rate_card: RateCard | None = None

    bill: Bill | None = None
    report: AuditReport | None = None
    recommendation: Recommendation | None = None
    headline: str = ""
    draft: Draft | None = None

    approved: bool = False
    submitted_ref: str | None = None
    trail: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        self.trail.append(f"{datetime.now():%H:%M:%S}  {msg}")

    # --- routing predicates, used by graph edge conditions ---

    @property
    def has_findings(self) -> bool:
        return bool(self.report and self.report.discrepancies)

    @property
    def is_actionable(self) -> bool:
        """Worth waking a human for."""
        if not self.report or not self.report.discrepancies:
            return False
        return (
            self.report.worst in (Severity.HIGH, Severity.MEDIUM)
            or self.report.total_at_stake >= Decimal("5.00")
            or any(d.rule == "zombie_subscription" for d in self.report.discrepancies)
        )

    def escalation(self) -> Escalation | None:
        if not self.is_actionable or self.bill is None or self.report is None:
            return None
        return Escalation(
            bill_id=self.bill.bill_id,
            vendor=self.bill.vendor,
            recommendation=self.recommendation or Recommendation.MONITOR,
            headline=self.headline or self.report.discrepancies[0].title,
            at_stake=self.report.total_at_stake,
            currency=self.bill.currency,
            discrepancies=self.report.discrepancies,
            draft=self.draft,
        )
