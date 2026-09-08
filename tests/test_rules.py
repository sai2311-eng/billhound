"""The audit engine is the only part allowed to state a euro figure, so it is
the part that gets tested hardest.

The first test is the most important one in the repo: a background agent that
cries wolf on a correct bill is worse than no agent at all.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from billhound.rules import audit
from billhound.schemas import Bill, Cadence, LineItem, RateCard, Severity


def li(desc: str, unit: str, qty: str = "1", code: str | None = None) -> LineItem:
    q, u = Decimal(qty), Decimal(unit)
    return LineItem(description=desc, quantity=q, unit_price=u, amount=q * u, code=code)


def bill(items: list[LineItem], total: str | None = None, **kw) -> Bill:
    items = items or []
    computed = sum((i.amount for i in items), Decimal(0))
    return Bill(
        bill_id=kw.pop("bill_id", "INV-100"),
        vendor=kw.pop("vendor", "Stadtwerke"),
        issued=kw.pop("issued", date(2026, 9, 1)),
        total=Decimal(total) if total is not None else computed,
        line_items=items,
        **kw,
    )


# --- the silence guarantee -------------------------------------------------

def test_clean_bill_raises_nothing():
    """A correct bill must be completely silent - no false positives."""
    b = bill([li("Grundpreis", "12.50"), li("Arbeitspreis kWh", "0.32", "180")])
    assert audit(b, history=[], rate_card=None) == []


def test_clean_bill_stays_silent_against_history_and_contract():
    prior = [
        bill([li("Grundpreis", "12.50"), li("Arbeitspreis kWh", "0.32", "170")],
             bill_id=f"INV-0{n}", issued=date(2026, n, 1))
        for n in (6, 7, 8)
    ]
    b = bill([li("Grundpreis", "12.50"), li("Arbeitspreis kWh", "0.32", "180")])
    card = RateCard(
        vendor="Stadtwerke",
        expected_unit_prices={"Grundpreis": Decimal("12.50"),
                              "Arbeitspreis kWh": Decimal("0.32")},
    )
    assert audit(b, prior, card) == []


# --- individual rules ------------------------------------------------------

def test_arithmetic_mismatch_flags_the_gap():
    b = bill([li("Grundpreis", "12.50"), li("Arbeitspreis kWh", "0.32", "100")], total="80.00")
    found = audit(b)
    assert [d.rule for d in found] == ["arithmetic_mismatch"]
    assert found[0].severity is Severity.HIGH
    assert found[0].delta == Decimal("35.50")  # 80.00 - 44.50


def test_arithmetic_mismatch_tolerates_rounding():
    b = bill([li("Service", "10.005")], total="10.01")
    assert audit(b) == []


def test_duplicate_line_item_counts_only_the_extras():
    b = bill([li("Setup fee", "49.00"), li("Setup fee", "49.00"), li("Setup fee", "49.00")])
    found = [d for d in audit(b) if d.rule == "duplicate_line_item"]
    assert len(found) == 1
    assert found[0].delta == Decimal("98.00")  # two extras, not three


def test_silent_price_hike_reports_percent_and_delta():
    prior = [bill([li("Arbeitspreis kWh", "0.30", "100")],
                  bill_id="INV-08", issued=date(2026, 8, 1))]
    b = bill([li("Arbeitspreis kWh", "0.39", "100")])
    found = [d for d in audit(b, prior) if d.rule == "silent_price_hike"]
    assert len(found) == 1
    assert found[0].delta == Decimal("9.00")  # 0.09 x 100
    assert found[0].severity is Severity.HIGH  # 30% jump
    assert "30.0%" in found[0].title


def test_price_drop_is_not_a_discrepancy():
    prior = [bill([li("Arbeitspreis kWh", "0.40", "100")],
                  bill_id="INV-08", issued=date(2026, 8, 1))]
    b = bill([li("Arbeitspreis kWh", "0.30", "100")])
    assert [d for d in audit(b, prior) if d.rule == "silent_price_hike"] == []


def test_off_contract_rate_uses_the_vendor_code_when_description_differs():
    card = RateCard(vendor="Stadtwerke", expected_unit_prices={"AP-01": Decimal("0.32")})
    b = bill([li("Arbeitspreis Strom kWh", "0.45", "200", code="AP-01")])
    found = [d for d in audit(b, [], card) if d.rule == "off_contract_rate"]
    assert len(found) == 1
    assert found[0].delta == Decimal("26.00")  # 0.13 x 200


def test_new_unexplained_fee_needs_two_prior_bills():
    prior_one = [bill([li("Grundpreis", "12.50")], bill_id="INV-08", issued=date(2026, 8, 1))]
    b = bill([li("Grundpreis", "12.50"), li("Mahngebuehr", "9.90")])
    assert [d for d in audit(b, prior_one) if d.rule == "new_unexplained_fee"] == []

    prior_two = [
        *prior_one,
        bill([li("Grundpreis", "12.50")], bill_id="INV-07", issued=date(2026, 7, 1)),
    ]
    found = [d for d in audit(b, prior_two) if d.rule == "new_unexplained_fee"]
    assert len(found) == 1
    assert found[0].delta == Decimal("9.90")


def test_vat_inconsistent_checks_against_net():
    card = RateCard(vendor="Stadtwerke", vat_rate=Decimal("0.19"))
    b = bill([li("Service", "100.00"), li("MwSt 19%", "25.00")])
    found = [d for d in audit(b, [], card) if d.rule == "vat_inconsistent"]
    assert len(found) == 1
    assert found[0].delta == Decimal("6.00")  # 25.00 charged vs 19.00 expected


def test_correct_vat_is_silent():
    card = RateCard(vendor="Stadtwerke", vat_rate=Decimal("0.19"))
    b = bill([li("Service", "100.00"), li("MwSt 19%", "19.00")])
    assert [d for d in audit(b, [], card) if d.rule == "vat_inconsistent"] == []


def test_zombie_subscription_needs_a_long_streak():
    def past(n):
        return bill([li("Pro plan", "14.99")], vendor="StreamCo",
                    bill_id=f"S-{n}", issued=date(2026, n, 1), cadence=Cadence.MONTHLY)

    b = bill([li("Pro plan", "14.99")], vendor="StreamCo", cadence=Cadence.MONTHLY)
    assert [d for d in audit(b, [past(n) for n in range(3, 7)])
            if d.rule == "zombie_subscription"] == []

    found = [d for d in audit(b, [past(n) for n in range(2, 9)])
             if d.rule == "zombie_subscription"]
    assert len(found) == 1
    assert found[0].delta == Decimal(0)  # nothing is owed back, it is a decision to make


def test_one_off_bill_is_never_a_zombie():
    b = bill([li("Installation", "199.00")], cadence=Cadence.ONE_OFF)
    history = [bill([li("Installation", "199.00")], bill_id=f"X-{n}", issued=date(2026, n, 1))
               for n in range(1, 9)]
    assert [d for d in audit(b, history) if d.rule == "zombie_subscription"] == []


# --- ordering --------------------------------------------------------------

def test_findings_are_ordered_by_severity_then_money():
    card = RateCard(vendor="Stadtwerke", expected_unit_prices={"Grundpreis": Decimal("10.00")})
    prior = [bill([li("Grundpreis", "10.00")], bill_id="INV-08", issued=date(2026, 8, 1)),
             bill([li("Grundpreis", "10.00")], bill_id="INV-07", issued=date(2026, 7, 1))]
    b = bill([li("Grundpreis", "30.00"), li("Servicepauschale", "5.00"),
              li("Servicepauschale", "5.00")])
    found = audit(b, prior, card)
    sev = [d.severity for d in found]
    assert sev == sorted(sev, key=[Severity.HIGH, Severity.MEDIUM,
                                   Severity.LOW, Severity.INFO].index)
    highs = [d.delta for d in found if d.severity is Severity.HIGH]
    assert highs == sorted(highs, reverse=True)


@pytest.mark.parametrize("empty", [[], None])
def test_empty_bill_and_no_history_do_not_crash(empty):
    b = bill([], total="0.00")
    assert audit(b, empty, None) == []


# --- the money is counted once --------------------------------------------

def test_same_overcharge_is_not_counted_twice():
    """A rate rise that also breaks the contract is two findings, one overcharge."""
    prior = [bill([li("Arbeitspreis kWh", "0.32", "200")],
                  bill_id="INV-08", issued=date(2026, 8, 1))]
    card = RateCard(vendor="Stadtwerke",
                    expected_unit_prices={"Arbeitspreis kWh": Decimal("0.32")})
    b = bill([li("Arbeitspreis kWh", "0.38", "200")])
    found = audit(b, prior, card)

    rules = {d.rule for d in found}
    assert {"silent_price_hike", "off_contract_rate"} <= rules  # both still reported

    total = sum(d.delta for d in found)
    assert total == Decimal("12.00")  # 0.06 x 200, counted once - not 24.00

    zeroed = [d for d in found if d.delta == 0 and d.line_item]
    assert zeroed and "counted once" in zeroed[0].evidence


def test_dedupe_keeps_the_larger_claim():
    prior = [bill([li("Grundpreis", "10.00")], bill_id="INV-08", issued=date(2026, 8, 1))]
    card = RateCard(vendor="Stadtwerke", expected_unit_prices={"Grundpreis": Decimal("8.00")})
    b = bill([li("Grundpreis", "14.00")])
    found = audit(b, prior, card)
    survivor = max(found, key=lambda d: d.delta)
    assert survivor.rule == "off_contract_rate"   # 14 - 8 = 6.00 beats 14 - 10 = 4.00
    assert survivor.delta == Decimal("6.00")
    assert sum(d.delta for d in found) == Decimal("6.00")


def test_distinct_line_items_are_both_counted():
    b = bill([li("Fee A", "5.00"), li("Fee A", "5.00"),
              li("Fee B", "3.00"), li("Fee B", "3.00")])
    found = audit(b)
    assert sum(d.delta for d in found) == Decimal("8.00")  # 5.00 + 3.00


def test_severity_breaks_a_delta_tie():
    """Equal claims: the HIGH keeps the money, the MEDIUM keeps only its evidence."""
    prior = [bill([li("Grundpreis", "12.50")], bill_id="INV-08", issued=date(2026, 8, 1))]
    card = RateCard(vendor="Stadtwerke",
                    expected_unit_prices={"Grundpreis": Decimal("12.50")})
    b = bill([li("Grundpreis", "14.90")])
    found = audit(b, prior, card)

    keeper = next(d for d in found if d.delta > 0)
    assert keeper.rule == "off_contract_rate"
    assert keeper.severity is Severity.HIGH
    assert keeper.delta == Decimal("2.40")
    assert sum(d.delta for d in found) == Decimal("2.40")


# --- tax lines are derived, not negotiated ---------------------------------

def test_vat_line_is_not_reported_as_a_price_hike():
    """VAT rises because the charges above it rose. Reporting it restates the
    same overcharge under a misleading name and inflates the total at stake."""
    prior = [bill([li("Service", "100.00"), li("MwSt 19%", "19.00")],
                  bill_id="INV-08", issued=date(2026, 8, 1))]
    b = bill([li("Service", "150.00"), li("MwSt 19%", "28.50")])
    hikes = [d for d in audit(b, prior) if d.rule == "silent_price_hike"]
    assert [d.line_item for d in hikes] == ["Service"]


def test_vat_line_is_not_reported_as_a_new_fee():
    prior = [bill([li("Service", "100.00")], bill_id=f"INV-0{n}", issued=date(2026, n, 1))
             for n in (7, 8)]
    b = bill([li("Service", "100.00"), li("USt 19%", "19.00")])
    assert [d for d in audit(b, prior) if d.rule == "new_unexplained_fee"] == []


def test_tax_line_detected_by_code_even_with_an_odd_description():
    prior = [bill([li("Service", "100.00"), li("Steuer", "19.00", code="VAT")],
                  bill_id="INV-08", issued=date(2026, 8, 1))]
    b = bill([li("Service", "100.00"), li("Steuer", "40.00", code="VAT")])
    assert [d for d in audit(b, prior) if d.rule == "silent_price_hike"] == []


def test_vat_correctness_is_still_checked():
    """Skipping VAT in the price rules must not stop vat_inconsistent working."""
    card = RateCard(vendor="Stadtwerke", vat_rate=Decimal("0.19"))
    b = bill([li("Service", "100.00"), li("MwSt 19%", "30.00")])
    found = [d for d in audit(b, [], card) if d.rule == "vat_inconsistent"]
    assert len(found) == 1
    assert found[0].delta == Decimal("11.00")
