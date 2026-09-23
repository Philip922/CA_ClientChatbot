"""Graph state.

`messages` accumulates across iterations — LLM turns and `ToolMessage` results
alike — because the model needs the tool output of iteration N to decide
iteration N+1. `add_messages` appends rather than replaces.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    # Incremented on every orchestrator pass and checked before each tool-call
    # decision, so a loop cannot outlive MAX_ITERATIONS.
    iterations: int
    escalation_reason: str | None
