"""A scripted stand-in for Bedrock.

This is **not** a language model and does not pretend to be one. It implements
the Strands `Model` interface and replies with fixed, rule-derived tool calls,
so the graph, the tools, the conditional edge and the human-in-the-loop gate can
all be exercised end to end with no AWS account and no network.

Two jobs:

1. Tests. `billhound watch` is the path that actually matters, and testing it
   only against live Bedrock means never testing it at all.
2. Insurance. If Bedrock is unreachable, `billhound watch --offline` still
   demonstrates the full pipeline - clearly labelled as scripted, because
   passing this off as model output would be a lie.

What it cannot do is the part that genuinely needs a model: reading a document
it has never seen, and writing a dispute letter in the vendor's language.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterable
from pathlib import Path
from typing import Any, TypeVar

from strands.models.model import Model

from .case import Case
from .fallback_parser import parse as fallback_parse
from .schemas import Recommendation

T = TypeVar("T")

#: Each agent gets a handful of turns before the stub gives up. Without this a
#: tool that never changes the case (a dry-run submit, say) loops forever.
MAX_TURNS_PER_AGENT = 6


def _tool_use(name: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockStart": {
            "start": {"toolUse": {"toolUseId": f"tu-{uuid.uuid4().hex[:12]}", "name": name}}}},
        {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(payload)}}}},
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "tool_use"}},
    ]


def _say(text: str) -> list[dict[str, Any]]:
    return [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"delta": {"text": text}}},
        {"contentBlockStop": {}},
        {"messageStop": {"stopReason": "end_turn"}},
    ]


class ScriptedModel(Model):
    """Answers based on which tools the calling agent holds, and on what the
    case already contains.

    Each of the four agents has a distinct toolset, so one instance stands in
    for all of them without being told which node is asking. Routing on case
    state rather than on turn count means a retried tool call does not desync
    the script.
    """

    def __init__(self, case: Case, vendor: str):
        self._case = case
        self._vendor = vendor
        self._config: dict[str, Any] = {"model_id": "scripted-offline"}
        self._turns: dict[str, int] = {}

    def get_config(self) -> Any:
        return self._config

    def update_config(self, **model_config: Any) -> None:
        self._config.update(model_config)

    async def structured_output(
        self, output_model: type[T], prompt: list[Any], system_prompt: str | None = None, **kw: Any
    ) -> AsyncGenerator[dict[str, Any], None]:
        raise NotImplementedError("ScriptedModel does not do structured output.")
        yield  # pragma: no cover - makes this an async generator

    async def stream(
        self,
        messages: list[Any],
        tool_specs: list[Any] | None = None,
        system_prompt: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterable[Any]:
        names = frozenset(spec["name"] for spec in (tool_specs or []))
        key = ",".join(sorted(names))
        self._turns[key] = self._turns.get(key, 0) + 1

        if self._turns[key] > MAX_TURNS_PER_AGENT:
            for event in _say("[scripted] turn budget exhausted; stopping."):
                yield event
            return

        for event in self._next_turn(names):
            yield event

    # --- the script ---------------------------------------------------------

    def _next_turn(self, names: frozenset[str]) -> list[dict[str, Any]]:
        case = self._case

        if "record_extracted_bill" in names:
            if case.bill is None:
                return self._extract()
            return _say(f"[scripted] Transcribed invoice {case.bill.bill_id} as printed.")

        if "run_audit" in names:
            if case.report is None:
                return _tool_use("run_audit", {})
            return _say(self._audit_summary())

        if names & {"record_decision", "save_draft"}:
            if case.recommendation is None and "record_decision" in names:
                return _tool_use("record_decision", self._decision())
            if case.draft is None and "save_draft" in names and case.report is not None:
                return _tool_use("save_draft", self._draft())
            return _say("[scripted] Decision recorded and draft saved. Nothing sent.")

        if "submit_to_portal" in names:
            if case.submitted_ref is None and self._turns.get(",".join(sorted(names)), 0) == 1:
                return _tool_use("submit_to_portal", {"confirm": True})
            return _say("[scripted] Submission step complete.")

        return _say("[scripted] nothing to do.")

    def _extract(self) -> list[dict[str, Any]]:
        bill = fallback_parse(Path(self._case.source), self._vendor)
        if bill is None:
            return _say(
                "[scripted] This document is not in the sample layout. Reading an "
                "arbitrary document needs a real model - run without --offline."
            )
        payload = json.loads(bill.model_dump_json(exclude={"line_item_sum", "source_file"}))
        return _tool_use("record_extracted_bill", {"bill_json": json.dumps(payload)})

    def _audit_summary(self) -> str:
        report = self._case.report
        if not report.discrepancies:
            return "[scripted] The bill is clean. Nothing needs human attention."
        return (
            f"[scripted] The audit found {len(report.discrepancies)} discrepancies worth "
            f"{report.total_at_stake:.2f} {report.bill.currency}. "
            f"Most serious severity: {report.worst.value}."
        )

    def _decision(self) -> dict[str, str]:
        report = self._case.report
        if report is None or not report.discrepancies:
            return {"recommendation": "ignore", "headline": "Nothing found."}
        zombie = any(d.rule == "zombie_subscription" for d in report.discrepancies)
        rec = Recommendation.CANCEL if zombie else Recommendation.DISPUTE
        if any(d.delta > 0 for d in report.discrepancies):
            headline = (
                f"{self._vendor} overcharged {report.total_at_stake:.2f} "
                f"{report.bill.currency} on invoice {report.bill.bill_id}."
            )
        else:
            headline = f"{report.discrepancies[0].title}."
        return {"recommendation": rec.value, "headline": headline[:140]}

    def _draft(self) -> dict[str, str]:
        report = self._case.report
        bill = report.bill
        lines = [
            "Sehr geehrte Damen und Herren,",
            "",
            f"zu Rechnung {bill.bill_id} vom {bill.issued} "
            f"(Kundennummer {bill.account_ref or 'n/a'}) habe ich folgende Beanstandungen:",
            "",
        ]
        charged = [d for d in report.discrepancies if d.delta > 0]
        for n, d in enumerate(charged or report.discrepancies, start=1):
            lines.append(f"{n}. {d.title} - {d.evidence}")
        lines += [
            "",
            f"Insgesamt beanstande ich {report.total_at_stake:.2f} {bill.currency}. "
            "Ich bitte um Pruefung und Korrektur der Rechnung.",
            "",
            "Mit freundlichen Gruessen",
        ]
        return {
            "channel": "email",
            "recipient": f"rechnung@{self._vendor.split()[0].lower()}.example",
            "subject": f"Beanstandung Rechnung {bill.bill_id}",
            "body": "\n".join(lines),
        }


def make_offline_model(case: Case, vendor: str) -> ScriptedModel:
    return ScriptedModel(case, vendor)
