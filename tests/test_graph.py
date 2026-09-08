"""End-to-end tests for the agent graph, with no AWS account.

`billhound watch` is the path that matters, so it needs to be tested. These
drive the real GraphBuilder graph, the real tools and the real HumanInTheLoop
intervention against a scripted Model implementation - only the model is fake.

The two behaviours worth guarding are the two that are easy to break silently:
the conditional edge that keeps a clean bill away from the human, and the gate
that keeps the agent from acting without one.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from strands import Agent
from strands.vended_interventions.hitl import HumanInTheLoop

from billhound.agents import EXECUTOR_PROMPT
from billhound.case import Case
from billhound.graph import run_case
from billhound.offline_model import make_offline_model
from billhound.schemas import Recommendation
from billhound.store import load_history, load_rate_card
from billhound.tools import AUTO_APPROVED, build_tools

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def make_case(folder: str) -> Case:
    d = SAMPLES / folder
    doc = next((d / "inbox").iterdir())
    return Case(
        source=str(doc),
        raw_text=doc.read_text(encoding="utf-8"),
        history=load_history(d),
        rate_card=load_rate_card(d),
    )


def drive(folder: str, vendor: str):
    case = make_case(folder)
    result, _agents = asyncio.run(run_case(case, model=make_offline_model(case, vendor)))
    return case, result


# --- the conditional edge --------------------------------------------------

def test_clean_bill_never_reaches_the_strategist():
    """The whole product in one assertion: a correct bill stops at the auditor."""
    case, result = drive("kabelnetz", "Kabelnetz Bayern")
    visited = [n.node_id for n in result.execution_order]

    assert visited == ["extractor", "auditor"]
    assert "strategist" not in visited
    assert case.escalation() is None
    assert case.draft is None


def test_overcharged_bill_reaches_the_strategist_and_gets_a_draft():
    case, result = drive("stadtwerke", "Stadtwerke Muenchen")
    visited = [n.node_id for n in result.execution_order]

    assert visited == ["extractor", "auditor", "strategist"]
    esc = case.escalation()
    assert esc is not None
    assert esc.recommendation is Recommendation.DISPUTE
    assert esc.at_stake == case.report.total_at_stake
    assert case.draft is not None and case.draft.body


def test_zombie_subscription_is_escalated_as_a_cancellation():
    case, _ = drive("streamco", "StreamCo")
    esc = case.escalation()
    assert esc is not None
    assert esc.recommendation is Recommendation.CANCEL
    assert esc.at_stake == 0  # nothing owed back; it is still a decision to make


def test_the_figures_the_agent_reports_come_from_the_rule_engine():
    """The agent may relay the audit, never restate it."""
    from billhound.rules import audit

    case, _ = drive("stadtwerke", "Stadtwerke Muenchen")
    independent = audit(case.bill, case.history, case.rate_card)

    assert [d.rule for d in case.report.discrepancies] == [d.rule for d in independent]
    assert case.report.total_at_stake == sum(d.delta for d in independent)


def test_extraction_copies_the_wrong_total_rather_than_fixing_it():
    """Silently correcting the invoice would destroy the evidence."""
    case, _ = drive("stadtwerke", "Stadtwerke Muenchen")
    assert case.bill.total != case.bill.line_item_sum
    assert any(d.rule == "arithmetic_mismatch" for d in case.report.discrepancies)


# --- the human-in-the-loop gate --------------------------------------------

def test_submit_is_not_on_the_auto_approved_list():
    assert "submit_to_portal" not in AUTO_APPROVED
    case = make_case("stadtwerke")
    assert {t.tool_name for t in build_tools(case)} - set(AUTO_APPROVED) == {"submit_to_portal"}


def executor_with(case: Case, answer: str) -> tuple[Agent, list[str]]:
    """An executor whose approval prompt is answered by `answer`."""
    asked: list[str] = []

    def ask(prompt: str) -> str:
        asked.append(prompt)
        return answer

    tools = {t.tool_name: t for t in build_tools(case)}
    agent = Agent(
        name="executor",
        model=make_offline_model(case, "Stadtwerke Muenchen"),
        system_prompt=EXECUTOR_PROMPT,
        tools=[tools["submit_to_portal"]],
        interventions=[HumanInTheLoop(allowed_tools=AUTO_APPROVED, ask=ask)],
    )
    return agent, asked


def test_declining_the_gate_means_nothing_is_submitted():
    case, _ = drive("stadtwerke", "Stadtwerke Muenchen")
    agent, asked = executor_with(case, "no")

    asyncio.run(agent.invoke_async("Submit the approved dispute."))

    assert asked, "the human was never asked - the gate did not fire"
    assert case.submitted_ref is None


def test_approving_the_gate_still_dry_runs_without_the_live_flag(monkeypatch):
    """Two locks, not one: approval opens the gate, BILLHOUND_LIVE arms the action."""
    monkeypatch.delenv("BILLHOUND_LIVE", raising=False)
    case, _ = drive("stadtwerke", "Stadtwerke Muenchen")
    agent, asked = executor_with(case, "yes")

    asyncio.run(agent.invoke_async("Submit the approved dispute."))

    assert asked
    assert case.submitted_ref is None
    assert any("dry-run" in line for line in case.trail)


def test_both_locks_open_lets_the_submission_through(monkeypatch):
    monkeypatch.setenv("BILLHOUND_LIVE", "1")
    case, _ = drive("stadtwerke", "Stadtwerke Muenchen")
    agent, asked = executor_with(case, "yes")

    asyncio.run(agent.invoke_async("Submit the approved dispute."))

    assert asked
    assert case.submitted_ref == "BH-SWM-2026-09"


@pytest.mark.parametrize("folder,vendor", [
    ("kabelnetz", "Kabelnetz Bayern"),
    ("stadtwerke", "Stadtwerke Muenchen"),
    ("streamco", "StreamCo"),
])
def test_every_sample_runs_without_error(folder, vendor):
    case, result = drive(folder, vendor)
    assert str(result.status).endswith("COMPLETED")
    assert case.bill is not None
    assert case.report is not None
