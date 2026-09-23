"""`escalate_to_human` — static handoff response, no external calls."""

from __future__ import annotations

from typing import Literal

from langchain_core.tools import tool

from config import get_settings
from orchestrator.prompts import ESCALATION_REASONS, escalation_message, tool_description

Reason = Literal[
    "pricing", "existing_client", "custom_scope", "user_requested", "low_confidence"
]


def build_handoff(reason: str, conversation_summary: str = "") -> dict:
    """The `{booking_link, message, summary}` payload, from templates only.

    Shared with the escalate node so a model-invoked escalation and an
    iteration-cap escalation produce byte-identical text.
    """
    normalised = reason if reason in ESCALATION_REASONS else "low_confidence"
    return {
        "reason": normalised,
        "booking_link": get_settings().booking_link,
        "message": escalation_message(normalised),
        "summary": conversation_summary,
    }


@tool(
    "escalate_to_human",
    description=tool_description("escalate_to_human"),
    response_format="content_and_artifact",
)
def escalate_to_human(reason: Reason, conversation_summary: str) -> tuple[str, dict]:
    """Hand the conversation to the Cadre AI team."""
    payload = build_handoff(reason, conversation_summary)
    content = (
        f"Escalation prepared (reason: {payload['reason']}). "
        f"Reply with this message and the booking link verbatim:\n\n"
        f"{payload['message']} {payload['booking_link']}"
    )
    return content, payload
