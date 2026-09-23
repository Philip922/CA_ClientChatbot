"""Orchestrator node, escalate node, and the routing between them."""

from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END

from config import get_settings
from orchestrator.prompts import orchestrator_system_prompt
from orchestrator.state import AgentState
from orchestrator.tools import TOOLS
from orchestrator.tools.escalation import build_handoff

logger = logging.getLogger(__name__)

ORCHESTRATOR = "orchestrator"
TOOL_NODE = "tools"
ESCALATE = "escalate"

ESCALATION_TOOL = "escalate_to_human"


@lru_cache(maxsize=1)
def get_llm():
    """The OpenRouter-backed chat model, tools already bound.

    Cached per container: building the client is cheap but the bound-tool schema
    conversion is not, and it is identical on every request.
    """
    from langchain_openai import ChatOpenAI

    settings = get_settings()
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")

    model = ChatOpenAI(
        model=settings.model_name,
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        temperature=settings.llm_temperature,
        timeout=settings.request_timeout,
        max_retries=1,
        streaming=True,
        # OpenRouter attributes traffic by these headers.
        default_headers={
            "HTTP-Referer": "https://cadreai.com",
            "X-Title": "Cadre AI Chatbot",
        },
    )
    return model.bind_tools(TOOLS)


async def orchestrator(state: AgentState) -> dict:
    """One LLM turn: either a tool call or the final answer."""
    messages = [SystemMessage(content=orchestrator_system_prompt()), *state["messages"]]
    response = await get_llm().ainvoke(messages)
    return {"messages": [response], "iterations": state.get("iterations", 0) + 1}


def escalate(state: AgentState) -> dict:
    """Produce the handoff message from a template, never from the model.

    Reached two ways: the model called `escalate_to_human` (its artifact is
    reused verbatim), or the iteration cap was hit (`low_confidence`). In the
    second case no further LLM call is made.
    """
    payload = _handoff_from_state(state)
    text = f"{payload['message']}\n\n{payload['booking_link']}"
    logger.info("escalating: reason=%s", payload["reason"])
    return {
        "messages": [AIMessage(content=text)],
        "escalation_reason": payload["reason"],
    }


def _handoff_from_state(state: AgentState) -> dict:
    messages = state.get("messages", [])
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and message.name == ESCALATION_TOOL:
            if isinstance(message.artifact, dict) and message.artifact.get("message"):
                return message.artifact
            break

    # Iteration cap, or a malformed tool result: fall back to low confidence and
    # summarise from the last thing the user said.
    last_user = next(
        (m.content for m in reversed(messages) if isinstance(m, HumanMessage)), ""
    )
    summary = f"User asked: {last_user}" if last_user else ""
    return build_handoff("low_confidence", summary)


def _tool_calls(message) -> list[dict]:
    return list(getattr(message, "tool_calls", None) or [])


def route_after_orchestrator(state: AgentState) -> str:
    """Tool call → tools; no tool call → the model answered; cap hit → escalate."""
    # The newest AIMessage rather than messages[-1]: the decision belongs to what
    # the model just said, and reading the tail position instead couples routing
    # to how the reducer happened to order the list.
    last_ai = next(
        (m for m in reversed(state.get("messages", [])) if isinstance(m, AIMessage)), None
    )
    if last_ai is None:
        return END

    calls = _tool_calls(last_ai)
    if not calls:
        # A final answer beats a forced escalation, even on the last iteration.
        return END

    escalating = any(call.get("name") == ESCALATION_TOOL for call in calls)
    if escalating:
        # Run the tool so the templated message comes from one code path.
        return TOOL_NODE

    if state.get("iterations", 0) >= get_settings().max_iterations:
        logger.info("iteration cap reached with a pending tool call — escalating")
        return ESCALATE

    return TOOL_NODE


def route_after_tools(state: AgentState) -> str:
    """An escalation result ends the run; anything else goes back to the model."""
    # Only the results just appended matter, and a parallel call can append
    # several at once — walk the trailing run of ToolMessages.
    trailing: list[ToolMessage] = []
    for message in reversed(state.get("messages", [])):
        if not isinstance(message, ToolMessage):
            break
        trailing.append(message)

    if any(message.name == ESCALATION_TOOL for message in trailing):
        return ESCALATE
    if state.get("iterations", 0) >= get_settings().max_iterations:
        return ESCALATE
    return ORCHESTRATOR
