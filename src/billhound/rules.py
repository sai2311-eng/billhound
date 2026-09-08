"""Deterministic audit rules.

No model runs here. Every number a human is asked to act on is produced by
arithmetic they could redo by hand, which is the only reason it is safe to put
a euro figure in an escalation headline.

Each rule takes (bill, history, rate_card) and yields Discrepancy objects.
Register a new one with @rule and it joins the engine.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterator
from decimal import Decimal

from .schemas import Bill, Cadence, Discrepancy, RateCard, Severity

Rule = Callable[[Bill, list[Bill], "RateCard | None"], Iterator[Discrepancy]]
REGISTRY: list[Rule] = []


def rule(fn: Rule) -> Rule:
    REGISTRY.append(fn)
    return fn


def _m(d: Decimal) -> str:
    return f"{d:.2f}"


_TAX_WORDS = ("vat", "ust", "mwst", "umsatzsteuer", "sales tax")


def _is_tax(li) -> bool:
    """Tax lines are derived, not negotiated.

    Their value moves whenever anything above them moves, so treating one as a
    price rise or a surprise fee reports the same overcharge a second time under
    a misleading name. Whether the tax itself is right is `vat_inconsistent`'s job.
    """
    if li.code and li.code.strip().casefold() in {"vat", "ust", "mwst"}:
        return True
    return any(w in li.description.casefold() for w in _TAX_WORDS)


@rule
def arithmetic_mismatch(bill, history, card):
    """The bill does not add up to itself."""
    if not bill.line_items:
        return
    tol = card.tolerance if card else Decimal("0.01")
    delta = bill.total - bill.line_item_sum
    if abs(delta) > tol:
        yield Discrepancy(
            rule="arithmetic_mismatch",
            severity=Severity.HIGH,
            title="Line items do not sum to the invoice total",
            evidence=(
                f"Line items total {_m(bill.line_item_sum)} {bill.currency}, but the invoice "
                f"is billed at {_m(bill.total)} {bill.currency} (difference {_m(delta)})."
            ),
            delta=max(delta, Decimal(0)),
        )


@rule
def duplicate_line_item(bill, history, card):
    """The same charge appears more than once on one statement."""
    seen = Counter((li.description.strip().casefold(), li.amount) for li in bill.line_items)
    for (desc, amount), count in seen.items():
        if count > 1 and amount > 0:
            extra = amount * (count - 1)
            yield Discrepancy(
                rule="duplicate_line_item",
                severity=Severity.HIGH,
                title=f"'{desc}' billed {count} times",
                evidence=(
                    f"'{desc}' at {_m(amount)} {bill.currency} appears {count} times on invoice "
                    f"{bill.bill_id}, overcharging {_m(extra)} {bill.currency}."
                ),
                delta=extra,
                line_item=desc,
            )


@rule
def silent_price_hike(bill, history, card):
    """A unit price moved without anyone agreeing to it."""
    if not history:
        return
    previous = max(history, key=lambda b: b.issued)
    before = {li.description.strip().casefold(): li for li in previous.line_items}
    for li in bill.line_items:
        if _is_tax(li):
            continue
        old = before.get(li.description.strip().casefold())
        if old is None or old.unit_price <= 0:
            continue
        jump = li.unit_price - old.unit_price
        if jump <= 0:
            continue
        pct = (jump / old.unit_price) * 100
        yield Discrepancy(
            rule="silent_price_hike",
            severity=Severity.MEDIUM if pct < 20 else Severity.HIGH,
            title=f"'{li.description}' rose {pct:.1f}% since {previous.issued}",
            evidence=(
                f"{_m(old.unit_price)} to {_m(li.unit_price)} {bill.currency} per unit between "
                f"invoice {previous.bill_id} ({previous.issued}) and {bill.bill_id} "
                f"({bill.issued}). No corresponding contract change on file."
            ),
            delta=jump * li.quantity,
            line_item=li.description,
        )


@rule
def off_contract_rate(bill, history, card):
    """The vendor is charging something other than the agreed rate."""
    if card is None or not card.expected_unit_prices:
        return
    expected = {k.strip().casefold(): v for k, v in card.expected_unit_prices.items()}
    for li in bill.line_items:
        if _is_tax(li):
            continue
        agreed = expected.get(li.description.strip().casefold())
        if agreed is None and li.code:
            agreed = expected.get(li.code.strip().casefold())
        if agreed is None:
            continue
        over = li.unit_price - agreed
        if over > card.tolerance:
            yield Discrepancy(
                rule="off_contract_rate",
                severity=Severity.HIGH,
                title=f"'{li.description}' billed above the agreed rate",
                evidence=(
                    f"Contract rate is {_m(agreed)} {bill.currency}; invoice {bill.bill_id} "
                    f"charges {_m(li.unit_price)} x{li.quantity} = {_m(li.amount)} "
                    f"{bill.currency}."
                ),
                delta=over * li.quantity,
                line_item=li.description,
            )


@rule
def new_unexplained_fee(bill, history, card):
    """A charge that has never appeared on this account before."""
    if len(history) < 2:
        return
    known = {li.description.strip().casefold() for past in history for li in past.line_items}
    for li in bill.line_items:
        if _is_tax(li) or li.description.strip().casefold() in known or li.amount <= 0:
            continue
        yield Discrepancy(
            rule="new_unexplained_fee",
            severity=Severity.MEDIUM,
            title=f"New charge never seen before: '{li.description}'",
            evidence=(
                f"'{li.description}' ({_m(li.amount)} {bill.currency}) does not appear on any of "
                f"the previous {len(history)} invoices from {bill.vendor}."
            ),
            delta=li.amount,
            line_item=li.description,
        )


@rule
def vat_inconsistent(bill, history, card):
    """A stated VAT line that does not match the stated VAT rate."""
    if card is None or card.vat_rate is None:
        return
    tags = ("vat", "ust", "mwst")
    vat_lines = [li for li in bill.line_items
                 if any(t in li.description.casefold() for t in tags)]
    if not vat_lines:
        return
    charged = sum((li.amount for li in vat_lines), Decimal(0))
    net = bill.line_item_sum - charged
    expected = (net * card.vat_rate).quantize(Decimal("0.01"))
    if abs(charged - expected) > card.tolerance:
        yield Discrepancy(
            rule="vat_inconsistent",
            severity=Severity.MEDIUM,
            title="VAT does not match the stated rate",
            evidence=(
                f"Net {_m(net)} {bill.currency} at {card.vat_rate * 100:.0f}% should be "
                f"{_m(expected)}, but {_m(charged)} was charged."
            ),
            delta=max(charged - expected, Decimal(0)),
        )


@rule
def zombie_subscription(bill, history, card):
    """A recurring charge that has quietly renewed for a long time."""
    if bill.cadence == Cadence.ONE_OFF:
        return
    streak = len([b for b in history if b.vendor == bill.vendor and b.total > 0])
    if streak < 6:
        return
    yield Discrepancy(
        rule="zombie_subscription",
        severity=Severity.LOW,
        title=f"{bill.vendor} has auto-renewed {streak + 1} periods running",
        evidence=(
            f"{streak + 1} consecutive {bill.cadence.value} charges from {bill.vendor}, most "
            f"recently {_m(bill.total)} {bill.currency}. "
            f"Annualised: {_m(bill.total * 12)} {bill.currency}."
        ),
        delta=Decimal(0),
    )


def _dedupe_money(found: list[Discrepancy]) -> list[Discrepancy]:
    """Stop two rules from billing the same euro twice.

    A price rise that also breaks the contract is genuinely two findings - the
    human wants to see both - but it is one overcharge. The weaker finding keeps
    its evidence and loses its delta, so `total_at_stake` stays honest.
    """
    by_item: dict[str, list[Discrepancy]] = {}
    for d in found:
        if d.line_item and d.delta > 0:
            by_item.setdefault(d.line_item.strip().casefold(), []).append(d)

    rank = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH]
    for group in by_item.values():
        if len(group) < 2:
            continue
        # Bigger claim wins; on a tie the more serious finding keeps the money,
        # so the human never sees a HIGH worth 0.00 beside a MEDIUM worth 12.60.
        keeper = max(group, key=lambda d: (d.delta, rank.index(d.severity)))
        for d in group:
            if d is keeper:
                continue
            d.evidence += f" (Amount counted once, under '{keeper.rule}'.)"
            d.delta = Decimal(0)
    return found


def audit(
    bill: Bill,
    history: list[Bill] | None = None,
    rate_card: RateCard | None = None,
) -> list[Discrepancy]:
    """Run every registered rule. Pure function: no I/O, no model, no network."""
    history = sorted(history or [], key=lambda b: b.issued)
    found: list[Discrepancy] = []
    for r in REGISTRY:
        found.extend(r(bill, history, rate_card))
    found = _dedupe_money(found)
    order = [Severity.HIGH, Severity.MEDIUM, Severity.LOW, Severity.INFO]
    return sorted(found, key=lambda d: (order.index(d.severity), -d.delta))
