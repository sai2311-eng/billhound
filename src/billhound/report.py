"""Rendering, for the rare moments the agent has something to say.

The design constraint: someone glancing at a phone should get the vendor, the
amount and the decision in the first line, and be able to stop reading.
"""

from __future__ import annotations

from .schemas import Escalation, Severity

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"
RED, YELLOW, BLUE, GREEN = "\033[31m", "\033[33m", "\033[34m", "\033[32m"

_COLOR = {
    Severity.HIGH: RED,
    Severity.MEDIUM: YELLOW,
    Severity.LOW: BLUE,
    Severity.INFO: DIM,
}


def render_silence(vendor: str, bill_id: str) -> str:
    return f"{DIM}  {vendor} {bill_id}: clean, nothing to report{RESET}"


def render_escalation(esc: Escalation) -> str:
    out: list[str] = []
    verb = {"dispute": "DISPUTE", "cancel": "CANCEL",
            "monitor": "MONITOR", "ignore": "IGNORE"}[esc.recommendation.value]

    out.append("")
    out.append(f"{BOLD}{RED}  {verb}{RESET}  {BOLD}{esc.vendor}{RESET}  "
               f"invoice {esc.bill_id}")
    out.append(f"  {BOLD}{esc.at_stake:.2f} {esc.currency}{RESET} at stake")
    out.append(f"  {esc.headline}")
    out.append("")

    for d in esc.discrepancies:
        colour = _COLOR[d.severity]
        money = f"{d.delta:.2f} {esc.currency}" if d.delta > 0 else "no refund due"
        out.append(f"  {colour}[{d.severity.value.upper():<6}]{RESET} {BOLD}{d.title}{RESET}")
        out.append(f"           {DIM}{d.evidence}{RESET}")
        out.append(f"           {DIM}rule: {d.rule}  |  {money}{RESET}")
        out.append("")

    if esc.draft:
        out.append(f"  {BOLD}Drafted {esc.draft.channel}{RESET} to {esc.draft.recipient}")
        out.append(f"  {DIM}Subject: {esc.draft.subject}{RESET}")
        out.extend(f"  {DIM}| {line}{RESET}" for line in esc.draft.body.splitlines())
        out.append("")
        out.append(f"  {YELLOW}Nothing has been sent. This is waiting on you.{RESET}")
    out.append("")
    return "\n".join(out)


def render_summary(seen: int, escalated: int, at_stake) -> str:
    quiet = seen - escalated
    return (
        f"\n{BOLD}  {seen} bills read.{RESET} "
        f"{GREEN}{quiet} handled silently.{RESET} "
        f"{RED}{escalated} need you.{RESET} "
        f"{BOLD}{at_stake:.2f} EUR at stake.{RESET}\n"
    )
