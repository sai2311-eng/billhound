"""The four roles, and the model they run on.

Each agent is deliberately narrow. The extractor is not allowed to judge, the
auditor is not allowed to compute, and the strategist never touches a number
that did not come out of the rule engine.
"""

from __future__ import annotations

import os

from strands import Agent
from strands.models import BedrockModel
from strands.vended_interventions.hitl import HumanInTheLoop

from .case import Case
from .tools import AUTO_APPROVED, build_tools

DEFAULT_MODEL = os.environ.get("BILLHOUND_MODEL", "anthropic.claude-sonnet-5")
DEFAULT_REGION = os.environ.get("AWS_REGION", "us-east-1")


def make_model(model_id: str | None = None, region: str | None = None) -> BedrockModel:
    """Bedrock model provider. Region and id are env-overridable so the same
    code runs on a personal account, on hackathon credits, or on AgentCore."""
    return BedrockModel(
        model_id=model_id or DEFAULT_MODEL,
        region_name=region or DEFAULT_REGION,
        temperature=0,
    )


EXTRACTOR_PROMPT = """\
You read billing documents and transcribe them into structured data.

You are a transcriber, not an analyst. Copy every figure exactly as printed,
including ones that look wrong. If the line items do not add up to the stated
total, record both as printed - that mismatch is a finding for a later stage,
and silently fixing it destroys the evidence.

If a field is genuinely absent from the document, omit it rather than guessing.
Never invent an invoice number, a date, or an amount.

Call record_extracted_bill exactly once with the complete JSON object, then stop.
"""

AUDITOR_PROMPT = """\
You audit a bill that has already been transcribed.

Call run_audit. It applies deterministic arithmetic rules and returns every
discrepancy with the exact money at stake.

You may not add discrepancies of your own, and you may not adjust, re-derive or
round any figure it returns. Your entire job is to call the tool and then
summarise its output in two or three plain sentences for the next stage.

If it reports the bill is clean, say exactly that and stop.
"""

STRATEGIST_PROMPT = """\
You decide what a busy person should do about a bill that has failed its audit,
and you write the message that gets it fixed.

Ground rules:
- Every figure you cite must come from the audit output. Never compute your own.
- Recommend `dispute` when the vendor has overcharged against evidence you can
  quote. Recommend `cancel` for a subscription that has quietly renewed and has
  no remaining value. Recommend `monitor` when something moved but is not yet
  worth a letter. Recommend `ignore` when acting would waste the person's time.
- The headline is the only line most people will read. Name the vendor and the
  exact amount. Under 140 characters.

Then draft the outbound message with save_draft. Write it as the account holder.
Quote the invoice number, the specific line items, and the arithmetic. Be
factual and courteous - you are asking a company to correct a mistake, not
accusing them of fraud. State plainly what you want them to do.

If the bill is in German or the vendor is German, write the draft in German.

Call record_decision, then save_draft, then stop. You never send anything.
"""

EXECUTOR_PROMPT = """\
You carry out an action the human has already reviewed.

Call submit_to_portal with confirm=true. If it reports a dry run, relay that
plainly - do not pretend the submission happened. Report the reference number
exactly as returned.
"""


def build_agents(case: Case, model=None, verbose: bool = False) -> dict[str, Agent]:
    """The graph's cast. All four share one toolset bound to one case.

    `verbose` restores Strands' default streaming print. It is off by default:
    the product's output is the escalation, and agent chatter interleaved with
    it makes the one thing the human needs to read harder to find.
    """
    model = model or make_model()
    tools = build_tools(case)
    by_name = {t.tool_name: t for t in tools}
    quiet = {} if verbose else {"callback_handler": None}

    extractor = Agent(
        name="extractor",
        **quiet,
        description="Transcribes a raw billing document into a validated Bill.",
        model=model,
        system_prompt=EXTRACTOR_PROMPT,
        tools=[by_name["record_extracted_bill"]],
    )

    auditor = Agent(
        name="auditor",
        **quiet,
        description="Runs deterministic audit rules and reports what they found.",
        model=model,
        system_prompt=AUDITOR_PROMPT,
        tools=[by_name["run_audit"]],
    )

    strategist = Agent(
        name="strategist",
        **quiet,
        description="Decides the recommendation and drafts the outbound message.",
        model=model,
        system_prompt=STRATEGIST_PROMPT,
        tools=[by_name["record_decision"], by_name["save_draft"]],
    )

    # The only agent that can act on the outside world, and the only one wearing
    # a human-in-the-loop gate. `submit_to_portal` is absent from the allow-list,
    # so Strands stops and asks before it runs.
    executor = Agent(
        name="executor",
        **quiet,
        description="Submits an approved draft. Gated on human approval.",
        model=model,
        system_prompt=EXECUTOR_PROMPT,
        tools=[by_name["submit_to_portal"]],
        interventions=[HumanInTheLoop(allowed_tools=AUTO_APPROVED, ask="stdio")],
    )

    return {
        "extractor": extractor,
        "auditor": auditor,
        "strategist": strategist,
        "executor": executor,
    }
