"""Graph assembly.

    orchestrator ──tool_calls──▶ tools ──▶ orchestrator   (loop, capped)
         │                         │
         │ no tool_calls           └─escalate_to_human──▶ escalate ──▶ END
         ▼                                                    ▲
        END                          iteration cap ────────────┘
"""

from __future__ import annotations

from functools import lru_cache

from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from orchestrator.nodes import (
    ESCALATE,
    ORCHESTRATOR,
    TOOL_NODE,
    escalate,
    orchestrator,
    route_after_orchestrator,
    route_after_tools,
)
from orchestrator.state import AgentState
from orchestrator.tools import TOOLS


def build_graph():
    builder = StateGraph(AgentState)

    builder.add_node(ORCHESTRATOR, orchestrator)
    # handle_tool_errors keeps a raising tool from killing the run: the error
    # comes back as a ToolMessage the model can react to.
    builder.add_node(TOOL_NODE, ToolNode(TOOLS, handle_tool_errors=True))
    builder.add_node(ESCALATE, escalate)

    builder.set_entry_point(ORCHESTRATOR)
    builder.add_conditional_edges(
        ORCHESTRATOR,
        route_after_orchestrator,
        {TOOL_NODE: TOOL_NODE, ESCALATE: ESCALATE, END: END},
    )
    builder.add_conditional_edges(
        TOOL_NODE,
        route_after_tools,
        {ORCHESTRATOR: ORCHESTRATOR, ESCALATE: ESCALATE},
    )
    builder.add_edge(ESCALATE, END)

    return builder.compile()


@lru_cache(maxsize=1)
def get_graph():
    """Compiled once per container — compilation is pure setup cost."""
    return build_graph()
