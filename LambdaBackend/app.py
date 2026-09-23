"""FastAPI app: routes, CORS, and the SSE translation layer.

The `/chat` handler's only real job is mapping LangGraph's `astream_events`
stream onto the SSE event vocabulary the Angular client already speaks
(`token`, `tool_start`, `tool_end`, `sources`, `error`, `[DONE]`).
"""

from __future__ import annotations

import json
import logging
from typing import AsyncIterator, Literal

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import AliasChoices, BaseModel, Field

from config import get_settings
from orchestrator.graph import get_graph
from orchestrator.nodes import ESCALATE

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("cadre.chat")

app = FastAPI(title="Cadre AI Chatbot", version="1.0.0")

# The only CORS layer: it answers preflight and puts the headers on the
# streaming response. Leave CORS off in the Function URL config — it adds its
# own headers to every response, and a duplicated Access-Control-Allow-Origin
# makes browsers reject the request.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().cors_allow_origin],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# --- request models ---------------------------------------------------------

# Request size caps. Every character is paid for in model tokens, and an
# unbounded history eventually overflows the model's context. The Angular client
# enforces tighter limits (MAX_MESSAGE_CHARS in message.model.ts, history caps in
# chat.service.ts), so these only reject requests that did not come from it.
MAX_MESSAGE_CHARS = 4_000
MAX_HISTORY_TURNS = 40
MAX_TURN_CHARS = 32_000


class HistoryTurn(BaseModel):
    role: Literal["user", "assistant", "agent"]
    content: str = Field(max_length=MAX_TURN_CHARS)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    history: list[HistoryTurn] = Field(default_factory=list, max_length=MAX_HISTORY_TURNS)


class FeedbackRequest(BaseModel):
    """Accepts both the field names the Angular client sends and the snake_case
    names in `backend-plan.md` section 6, so neither side has to change."""

    message_id: str = Field(validation_alias=AliasChoices("messageId", "message_id"))
    rating: Literal["up", "down"] = Field(validation_alias=AliasChoices("rating", "value"))
    content: str | None = Field(
        default=None,
        validation_alias=AliasChoices("content", "message_content"),
    )


# --- SSE helpers ------------------------------------------------------------


def sse(event: str, payload: dict) -> str:
    """One SSE record: an `event:` line, one `data:` line, a blank line."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


DONE = "data: [DONE]\n\n"


def _chunk_text(chunk) -> str:
    """Text out of an AIMessageChunk, whose content may be a string or blocks."""
    content = getattr(chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _sources_from_output(output) -> list[dict]:
    """Pull the source list off a tool result.

    Tools return `(content, artifact)`, so the artifact is where citations live.
    Falls back through the shapes a tool result can take rather than assuming
    one, since an errored ToolNode result is a plain string.
    """
    artifact = getattr(output, "artifact", None)
    if artifact is None and isinstance(output, tuple) and len(output) == 2:
        artifact = output[1]

    if isinstance(artifact, list):
        return [item for item in artifact if isinstance(item, dict)]
    if isinstance(artifact, dict):
        nested = artifact.get("sources")
        return nested if isinstance(nested, list) else []
    return []


def _history_messages(history: list[HistoryTurn]) -> list:
    return [
        HumanMessage(content=turn.content)
        if turn.role == "user"
        else AIMessage(content=turn.content)
        for turn in history
        if turn.content.strip()
    ]


async def chat_events(request: ChatRequest) -> AsyncIterator[str]:
    """Translate the graph's event stream into SSE records."""
    state = {
        "messages": [*_history_messages(request.history), HumanMessage(content=request.message)],
        "iterations": 0,
        "escalation_reason": None,
    }

    collected: list[dict] = []
    emitted_text = False

    try:
        async for event in get_graph().astream_events(state, version="v2"):
            kind = event["event"]

            if kind == "on_chat_model_stream":
                text = _chunk_text(event["data"].get("chunk"))
                if text:
                    emitted_text = True
                    yield sse("token", {"content": text})

            elif kind == "on_tool_start":
                yield sse("tool_start", {"tool": event["name"], "id": str(event["run_id"])})

            elif kind == "on_tool_end":
                sources = _sources_from_output(event["data"].get("output"))
                collected.extend(sources)
                yield sse(
                    "tool_end",
                    {"tool": event["name"], "id": str(event["run_id"]), "sources": sources},
                )

            elif kind == "on_tool_error":
                # Tools are written not to raise, but if one does the client
                # still needs its `tool_end` or the indicator stays spinning.
                yield sse(
                    "tool_end",
                    {"tool": event["name"], "id": str(event["run_id"]), "sources": []},
                )

            elif kind == "on_chain_end" and event.get("name") == ESCALATE:
                # The escalate node writes its message from a template, so it
                # produces no model tokens — stream its text here instead.
                for message in (event["data"].get("output") or {}).get("messages", []):
                    text = getattr(message, "content", "")
                    if isinstance(text, str) and text:
                        emitted_text = True
                        yield sse("token", {"content": text})

        if collected:
            yield sse("sources", {"sources": collected})

        if not emitted_text:
            # The client treats an empty message as a failure; say something.
            logger.warning("graph produced no text output")
            yield sse(
                "token",
                {
                    "content": "I could not put an answer together just now. "
                    f"You can reach the Cadre AI team directly: "
                    f"{get_settings().booking_link}"
                },
            )

    except Exception:  # noqa: BLE001 - the stream must not die silently
        logger.exception("chat stream failed")
        yield sse("error", {"message": "The agent hit an error. Please try again."})

    finally:
        yield DONE


# --- routes -----------------------------------------------------------------


@app.post("/chat")
async def chat(request: ChatRequest) -> StreamingResponse:
    return StreamingResponse(
        chat_events(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Stops CloudFront and any intermediate proxy from buffering the
            # stream into a single response.
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/feedback")
async def feedback(request: FeedbackRequest) -> dict:
    # No database in the PoC — CloudWatch is the store. One JSON line per rating
    # so it can be queried with Logs Insights.
    logger.info(
        "feedback %s",
        json.dumps(
            {
                "message_id": request.message_id,
                "rating": request.rating,
                "content": request.content,
            },
            ensure_ascii=False,
        ),
    )
    return {"status": "received"}
