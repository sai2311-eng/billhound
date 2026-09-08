"""Command line entry point.

Two modes, deliberately:

  billhound audit   deterministic engine only. No AWS, no model, no network.
                    Every euro figure the product will ever show comes from
                    this path, so it must be inspectable on its own.

  billhound watch   the full Strands agent graph on Bedrock. Reads arbitrary
                    documents, decides, and drafts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path

from .case import Case
from .fallback_parser import parse as fallback_parse
from .report import render_escalation, render_silence, render_summary
from .rules import audit
from .schemas import AuditReport, Recommendation
from .store import load_history, load_inbox, load_rate_card, vendor_dirs

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def _vendor_name(vendor_dir: Path) -> str:
    card = load_rate_card(vendor_dir)
    if card:
        return card.vendor
    history = load_history(vendor_dir)
    return history[0].vendor if history else vendor_dir.name.title()


def _targets(root: Path, only: str | None) -> list[Path]:
    dirs = vendor_dirs(root)
    if only:
        dirs = [d for d in dirs if only.casefold() in d.name.casefold()]
    return dirs


def cmd_audit(args) -> int:
    """Deterministic pass. Proves the numbers without spending a cent."""
    root = Path(args.samples)
    seen = escalated = 0
    at_stake = Decimal(0)
    payload = []

    for vendor_dir in _targets(root, args.vendor):
        vendor = _vendor_name(vendor_dir)
        history, card = load_history(vendor_dir), load_rate_card(vendor_dir)

        for doc in load_inbox(vendor_dir):
            bill = fallback_parse(doc, vendor)
            if bill is None:
                print(f"{DIM}  {doc.name}: not in the sample layout - "
                      f"use `billhound watch` for real documents{RESET}")
                continue

            seen += 1
            report = AuditReport(bill=bill, discrepancies=audit(bill, history, card))
            case = Case(source=str(doc), raw_text="", history=history, rate_card=card)
            case.bill, case.report = bill, report

            if not case.is_actionable:
                print(render_silence(vendor, bill.bill_id))
                continue

            escalated += 1
            at_stake += report.total_at_stake
            case.recommendation = (
                Recommendation.CANCEL
                if any(d.rule == "zombie_subscription" for d in report.discrepancies)
                else Recommendation.DISPUTE
            )
            charged = [d for d in report.discrepancies if d.delta > 0]
            if charged:
                case.headline = (
                    f"{vendor} overcharged {report.total_at_stake:.2f} {bill.currency} "
                    f"on invoice {bill.bill_id} across {len(charged)} "
                    f"{'item' if len(charged) == 1 else 'items'}."
                )
            else:
                case.headline = (
                    f"Invoice {bill.bill_id} is arithmetically correct. "
                    f"{report.discrepancies[0].title}."
                )
            esc = case.escalation()
            payload.append(json.loads(esc.model_dump_json()))
            if not args.json:
                print(render_escalation(esc))

    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(render_summary(seen, escalated, at_stake))
    return 1 if escalated else 0


async def _watch(args) -> int:
    from .graph import run_case
    from .offline_model import make_offline_model

    if args.offline:
        print(f"{DIM}  --offline: replies are scripted, not model output. "
              f"Exercises the graph, not the reasoning.{RESET}")

    root = Path(args.samples)
    seen = escalated = 0
    at_stake = Decimal(0)

    for vendor_dir in _targets(root, args.vendor):
        vendor = _vendor_name(vendor_dir)
        history, card = load_history(vendor_dir), load_rate_card(vendor_dir)

        for doc in load_inbox(vendor_dir):
            seen += 1
            case = Case(
                source=str(doc),
                raw_text=doc.read_text(encoding="utf-8"),
                history=history,
                rate_card=card,
            )
            print(f"{DIM}  reading {doc.name} ...{RESET}")
            model = make_offline_model(case, vendor) if args.offline else None
            try:
                await run_case(case, model=model, verbose=args.trace)
            except Exception as exc:
                print(f"  {doc.name}: agent run failed - {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                continue

            if args.trace:
                for line in case.trail:
                    print(f"{DIM}      {line}{RESET}")

            esc = case.escalation()
            if esc is None:
                bill_id = case.bill.bill_id if case.bill else doc.name
                print(render_silence(vendor, bill_id))
                continue

            escalated += 1
            at_stake += esc.at_stake
            print(render_escalation(esc))

    print(render_summary(seen, escalated, at_stake))
    return 1 if escalated else 0


def cmd_watch(args) -> int:
    return asyncio.run(_watch(args))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="billhound",
        description="An agent that reads every bill and only speaks when it matters.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    for name, fn, blurb in [
        ("audit", cmd_audit, "Deterministic rules only. No AWS, no model, no network."),
        ("watch", cmd_watch, "Full Strands agent graph on Bedrock."),
    ]:
        p = sub.add_parser(name, help=blurb, description=blurb)
        p.add_argument("--samples", default="samples",
                       help="Root directory of vendor folders (default: samples)")
        p.add_argument("--vendor", default=None,
                       help="Only process vendor folders matching this substring")
        p.set_defaults(func=fn)

    sub.choices["audit"].add_argument(
        "--json", action="store_true", help="Emit escalations as JSON instead of text")
    sub.choices["watch"].add_argument(
        "--trace", action="store_true", help="Print each agent's tool calls")
    sub.choices["watch"].add_argument(
        "--offline", action="store_true",
        help="Drive the graph with a scripted stub instead of Bedrock. No AWS needed. "
             "Proves the wiring, not the reasoning.")

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
