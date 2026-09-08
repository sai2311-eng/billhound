"""The graph.

    extractor  ->  auditor  ->  [findings worth a human?]  ->  strategist
                                          |
                                          +--> no: stop, say nothing

That conditional edge is the whole product. A bill that is merely correct ends
its life at the auditor and the human never hears about it.
"""

from __future__ import annotations

from strands.multiagent import GraphBuilder

from .agents import build_agents
from .case import Case


def build_graph(case: Case, model=None, verbose: bool = False):
    """Wire the cast into a graph whose routing depends on the case, not on prose."""
    agents = build_agents(case, model=model, verbose=verbose)

    builder = GraphBuilder()
    builder.add_node(agents["extractor"], "extractor")
    builder.add_node(agents["auditor"], "auditor")
    builder.add_node(agents["strategist"], "strategist")

    builder.add_edge("extractor", "auditor")
    builder.add_edge(
        "auditor",
        "strategist",
        condition=lambda state: case.is_actionable,
    )

    builder.set_entry_point("extractor")
    builder.set_node_timeout(120)
    builder.set_execution_timeout(600)
    return builder.build(), agents


TASK = """\
Here is a billing document to process.

Source: {source}
Vendor context: {vendor_hint}

--- BEGIN DOCUMENT ---
{raw}
--- END DOCUMENT ---
"""


async def run_case(case: Case, model=None, verbose: bool = False):
    """Push one bill through the graph. Returns (GraphResult, agents)."""
    graph, agents = build_graph(case, model=model, verbose=verbose)
    task = TASK.format(
        source=case.source,
        vendor_hint=(
            f"{len(case.history)} previous invoices on file"
            + (
                f"; a contract rate card is on file for {case.rate_card.vendor}"
                if case.rate_card
                else "; no rate card on file"
            )
        ),
        raw=case.raw_text,
    )
    result = await graph.invoke_async(task)
    return result, agents
