"""A deterministic parser for the sample invoice layout.

This is NOT the product's extraction path - real documents vary too much for a
regex, which is exactly why the extractor agent exists. This exists so the
audit engine can be demonstrated and tested with no AWS account, no network and
no model, and so the pipeline has something to fall back to when Bedrock is
unreachable mid-demo.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from .schemas import Bill, Cadence, LineItem

_ROW = re.compile(
    r"^\s*\d+\s+(?P<code>\S+)\s+(?P<desc>.+?)\s{2,}"
    r"(?P<qty>[\d.,]+)\s+(?P<unit>[\d.,]+)\s*EUR\s+(?P<amount>[\d.,]+)\s*EUR\s*$"
)
_FIELD = {
    "bill_id": re.compile(r"Rechnungsnummer:\s*(\S+)"),
    "account_ref": re.compile(r"Kundennummer:\s*(\S+)"),
    "issued": re.compile(r"Rechnungsdatum:\s*(\d{2}\.\d{2}\.\d{4})"),
    "due": re.compile(r"Faellig am:\s*(\d{2}\.\d{2}\.\d{4})"),
    "total": re.compile(r"RECHNUNGSBETRAG\s+([\d.,]+)\s*EUR"),
}


def _num(raw: str) -> Decimal:
    """German notation: 1.234,56 -> 1234.56"""
    return Decimal(raw.replace(".", "").replace(",", "."))


def _day(raw: str) -> date:
    return datetime.strptime(raw, "%d.%m.%Y").date()


def parse(path: Path, vendor: str) -> Bill | None:
    """Return a Bill, or None if the document is not in the sample layout."""
    text = path.read_text(encoding="utf-8")
    found = {k: m.group(1) for k, rx in _FIELD.items() if (m := rx.search(text))}
    if "bill_id" not in found or "total" not in found:
        return None

    items = [
        LineItem(
            description=m.group("desc").strip(),
            quantity=_num(m.group("qty")),
            unit_price=_num(m.group("unit")),
            amount=_num(m.group("amount")),
            code=m.group("code"),
        )
        for line in text.splitlines()
        if (m := _ROW.match(line))
    ]
    if not items:
        return None

    return Bill(
        bill_id=found["bill_id"],
        vendor=vendor,
        account_ref=found.get("account_ref"),
        issued=_day(found["issued"]) if "issued" in found else date.today(),
        due=_day(found["due"]) if "due" in found else None,
        total=_num(found["total"]),
        line_items=items,
        cadence=Cadence.MONTHLY,
        source_file=str(path),
    )
