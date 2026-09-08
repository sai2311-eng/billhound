"""Loading a vendor's file: what we already know before the new bill arrives.

A vendor folder looks like:

    samples/stadtwerke/
        ratecard.json      what the user believes they agreed to pay
        history.json       previously processed bills
        inbox/             raw documents not yet looked at
"""

from __future__ import annotations

import json
from pathlib import Path

from .schemas import Bill, RateCard


def load_history(vendor_dir: Path) -> list[Bill]:
    path = vendor_dir / "history.json"
    if not path.exists():
        return []
    return [Bill.model_validate(b) for b in json.loads(path.read_text(encoding="utf-8"))]


def load_rate_card(vendor_dir: Path) -> RateCard | None:
    path = vendor_dir / "ratecard.json"
    if not path.exists():
        return None
    return RateCard.model_validate(json.loads(path.read_text(encoding="utf-8")))


def load_inbox(vendor_dir: Path) -> list[Path]:
    inbox = vendor_dir / "inbox"
    if not inbox.exists():
        return []
    return sorted(p for p in inbox.iterdir()
                  if p.is_file() and p.suffix.lower() in {".txt", ".json", ".md"})


def vendor_dirs(root: Path) -> list[Path]:
    return sorted(p for p in root.iterdir() if p.is_dir() and (p / "inbox").exists())


def append_to_history(vendor_dir: Path, bill: Bill) -> None:
    """Fold a processed bill into history so the next run can compare against it."""
    path = vendor_dir / "history.json"
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    if any(b.get("bill_id") == bill.bill_id for b in existing):
        return
    existing.append(json.loads(bill.model_dump_json(exclude={"line_item_sum"})))
    path.write_text(json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8")
