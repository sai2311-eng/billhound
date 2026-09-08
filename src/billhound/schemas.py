"""Strict schemas passed between agent nodes.

Every hand-off in the graph is validated. An agent that hallucinates a field
fails here rather than three nodes later, next to a real bank account.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, computed_field


class Cadence(StrEnum):
    ONE_OFF = "one_off"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    ANNUAL = "annual"


class Severity(StrEnum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LineItem(BaseModel):
    description: str
    quantity: Decimal = Decimal(1)
    unit_price: Decimal
    amount: Decimal
    code: str | None = Field(None, description="Vendor's own fee/tariff code, verbatim.")


class Bill(BaseModel):
    """One statement from one vendor. The unit the whole system reasons about."""

    bill_id: str
    vendor: str
    account_ref: str | None = None
    issued: date
    due: date | None = None
    currency: str = "EUR"
    period_start: date | None = None
    period_end: date | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    total: Decimal
    cadence: Cadence = Cadence.ONE_OFF
    source_file: str | None = None

    @computed_field
    @property
    def line_item_sum(self) -> Decimal:
        return sum((li.amount for li in self.line_items), Decimal(0))


class Discrepancy(BaseModel):
    """A specific, evidenced disagreement with a bill.

    `evidence` must quote real numbers. It goes in front of a human and,
    eventually, in front of the vendor - so it carries its own proof.
    """

    rule: str
    severity: Severity
    title: str
    evidence: str
    delta: Decimal = Field(Decimal(0), description="Money at stake. Positive = overcharged.")
    line_item: str | None = None


class Recommendation(StrEnum):
    IGNORE = "ignore"
    MONITOR = "monitor"
    DISPUTE = "dispute"
    CANCEL = "cancel"


class AuditReport(BaseModel):
    bill: Bill
    discrepancies: list[Discrepancy] = Field(default_factory=list)

    @computed_field
    @property
    def total_at_stake(self) -> Decimal:
        return sum((d.delta for d in self.discrepancies), Decimal(0))

    @computed_field
    @property
    def worst(self) -> Severity:
        order = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH]
        return max((d.severity for d in self.discrepancies), key=order.index, default=Severity.INFO)


class Draft(BaseModel):
    """Outbound text awaiting a human. Never sent by the agent on its own."""

    channel: str = Field(description="email | portal_form | letter")
    recipient: str
    subject: str
    body: str
    portal_url: str | None = None


class Escalation(BaseModel):
    """What the human actually sees. Produced only when silence is wrong."""

    bill_id: str
    vendor: str
    recommendation: Recommendation
    headline: str
    at_stake: Decimal
    currency: str
    discrepancies: list[Discrepancy]
    draft: Draft | None = None


class RateCard(BaseModel):
    """What the user believes they agreed to pay.

    Supplied once per vendor. Without it the engine still catches internal
    inconsistencies and drift; with it, it also catches a vendor quietly
    leaving the contract.
    """

    vendor: str
    expected_unit_prices: dict[str, Decimal] = Field(default_factory=dict)
    expected_period_total: Decimal | None = None
    vat_rate: Decimal | None = Field(None, description="e.g. 0.19 for German USt.")
    tolerance: Decimal = Field(Decimal("0.01"), description="Rounding slack, absolute.")
