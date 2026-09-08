"""Strands tools, bound to one Case.

Anything that costs money or leaves the machine lives behind `submit_to_portal`
and `send_draft`, which are the only two tools kept out of the HITL allow-list.
"""

from __future__ import annotations

import json
from typing import Any

from strands import tool

from .case import Case
from .rules import audit
from .schemas import AuditReport, Bill, Draft, Recommendation


def build_tools(case: Case) -> list[Any]:
    """Return tools closed over `case`. One toolset per bill in flight."""

    @tool
    def record_extracted_bill(bill_json: str) -> str:
        """Store the structured bill you read out of the raw document.

        Args:
            bill_json: A JSON object with keys bill_id, vendor, issued (YYYY-MM-DD),
                due, currency, cadence, total, and line_items - a list of objects with
                description, quantity, unit_price, amount and optional code. Copy
                figures verbatim from the document. Never estimate a number.
        """
        try:
            payload = json.loads(bill_json)
        except json.JSONDecodeError as exc:
            return f"REJECTED - not valid JSON: {exc}. Re-read the document and try again."
        try:
            bill = Bill.model_validate(payload)
        except Exception as exc:
            return f"REJECTED - does not match the Bill schema: {exc}"

        bill.source_file = case.source
        case.bill = bill
        case.note(f"extracted {bill.bill_id} from {bill.vendor}, total {bill.total}")
        drift = bill.total - bill.line_item_sum
        return (
            f"Accepted bill {bill.bill_id} from {bill.vendor}: {len(bill.line_items)} line items, "
            f"stated total {bill.total} {bill.currency}, line items sum to {bill.line_item_sum} "
            f"(difference {drift}). Do not correct this difference - the auditor handles it."
        )

    @tool
    def run_audit() -> str:
        """Run the deterministic audit rules over the extracted bill.

        Uses arithmetic only - no judgement, no estimation. Call this once the bill
        has been recorded. Returns every discrepancy found, with the money at stake.
        """
        if case.bill is None:
            return "No bill recorded yet. Call record_extracted_bill first."
        found = audit(case.bill, case.history, case.rate_card)
        case.report = AuditReport(bill=case.bill, discrepancies=found)
        case.note(f"audit found {len(found)} discrepancies, {case.report.total_at_stake} at stake")
        if not found:
            return "Clean. No discrepancies. This bill needs no human attention."
        lines = [
            f"- [{d.severity.value.upper()}] {d.rule}: {d.title} | at stake "
            f"{d.delta} {case.bill.currency} | evidence: {d.evidence}"
            for d in found
        ]
        return (
            f"{len(found)} discrepancies, {case.report.total_at_stake} {case.bill.currency} "
            f"at stake:\n" + "\n".join(lines)
        )

    @tool
    def record_decision(recommendation: str, headline: str) -> str:
        """Commit to what the human should do about this bill.

        Args:
            recommendation: One of ignore, monitor, dispute, cancel.
            headline: One sentence, under 140 characters, naming the vendor and the
                exact amount at stake. This is all the human reads first.
        """
        try:
            rec = Recommendation(recommendation.strip().lower())
        except ValueError:
            return f"REJECTED - must be one of {[r.value for r in Recommendation]}."
        if len(headline) > 140:
            return f"REJECTED - headline is {len(headline)} chars, limit is 140."
        case.recommendation = rec
        case.headline = headline.strip()
        case.note(f"decision: {rec.value} - {headline}")
        return f"Recorded {rec.value}."

    @tool
    def save_draft(channel: str, recipient: str, subject: str, body: str,
                   portal_url: str = "") -> str:
        """Save outbound text for the human to review. Does not send anything.

        Args:
            channel: email, portal_form or letter.
            recipient: Vendor contact address, or the vendor name for a portal form.
            subject: Subject line.
            body: Full message. Cite the invoice id and every figure you are
                disputing. Be factual and quote the evidence - no threats, no
                speculation about the vendor's motives.
            portal_url: Where this would be submitted, if known.
        """
        if channel not in {"email", "portal_form", "letter"}:
            return "REJECTED - channel must be email, portal_form or letter."
        case.draft = Draft(
            channel=channel,
            recipient=recipient,
            subject=subject,
            body=body,
            portal_url=portal_url or None,
        )
        case.note(f"drafted {channel} to {recipient} ({len(body)} chars)")
        return f"Draft saved ({len(body)} chars). It will not be sent without human approval."

    @tool
    def submit_to_portal(confirm: bool = False) -> str:
        """Submit the saved draft to the vendor's portal. Irreversible.

        Gated behind human approval and refuses to run unless BILLHOUND_LIVE=1 is
        set in the environment. Without it, reports what it would have done.

        Args:
            confirm: Must be true.
        """
        import os

        if case.draft is None:
            return "Nothing to submit - no draft saved."
        if not confirm:
            return "Not submitted - confirm was false."
        if os.environ.get("BILLHOUND_LIVE") != "1":
            case.note("submit blocked: dry-run (BILLHOUND_LIVE unset)")
            return (
                f"DRY RUN. Would submit a {case.draft.channel} to {case.draft.recipient} "
                f"at {case.draft.portal_url or 'the vendor portal'}. "
                f"Set BILLHOUND_LIVE=1 to arm real submission."
            )
        ref = f"BH-{case.bill.bill_id if case.bill else 'unknown'}"
        case.submitted_ref = ref
        case.note(f"SUBMITTED {ref}")
        return f"Submitted. Reference {ref}."

    return [record_extracted_bill, run_audit, record_decision, save_draft, submit_to_portal]


#: Tools safe to run without asking. Everything else hits the HITL gate.
AUTO_APPROVED = ["record_extracted_bill", "run_audit", "record_decision", "save_draft"]
