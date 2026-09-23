"""The HTTP surface, including the exact SSE framing the Angular client parses."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

import app as app_module
from app import app


@pytest.fixture
def client():
    return TestClient(app)


class FakeGraph:
    """Replays a canned `astream_events` sequence."""

    def __init__(self, events: list[dict], error: Exception | None = None):
        self.events = events
        self.error = error
        self.state: dict | None = None

    async def astream_events(self, state, version="v2", **_):
        self.state = state
        for event in self.events:
            yield event
        if self.error:
            raise self.error


@pytest.fixture
def graph(monkeypatch):
    def install(events, error=None) -> FakeGraph:
        fake = FakeGraph(events, error)
        monkeypatch.setattr(app_module, "get_graph", lambda: fake)
        return fake

    return install


def records(body: str) -> list[tuple[str, str]]:
    """Parse the response the way the client's SseDecoder does."""
    out = []
    for block in body.split("\n\n"):
        if not block.strip():
            continue
        event, data = "message", []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
        out.append((event, "\n".join(data)))
    return out


def stream(client, **payload) -> list[tuple[str, str]]:
    response = client.post("/chat", json={"message": "hi", **payload})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    return records(response.text)


# --- health and feedback ----------------------------------------------------


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_feedback_accepts_the_angular_payload(client):
    response = client.post(
        "/feedback",
        json={"messageId": "abc-123", "rating": "up", "content": "Helpful answer"},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "received"}


def test_feedback_accepts_the_snake_case_payload(client):
    response = client.post(
        "/feedback",
        json={"message_id": "abc-123", "value": "down", "message_content": "Wrong"},
    )
    assert response.status_code == 200


def test_feedback_content_is_optional(client):
    assert client.post("/feedback", json={"messageId": "x", "rating": "up"}).status_code == 200


def test_feedback_rejects_an_unknown_rating(client):
    assert client.post("/feedback", json={"messageId": "x", "rating": "maybe"}).status_code == 422


def test_chat_rejects_an_empty_message(client):
    assert client.post("/chat", json={"message": ""}).status_code == 422


def test_chat_rejects_an_oversized_message(client):
    message = "x" * (app_module.MAX_MESSAGE_CHARS + 1)
    assert client.post("/chat", json={"message": message}).status_code == 422


def test_chat_rejects_too_many_history_turns(client):
    history = [{"role": "user", "content": "hi"}] * (app_module.MAX_HISTORY_TURNS + 1)
    assert client.post("/chat", json={"message": "hi", "history": history}).status_code == 422


def test_chat_rejects_an_oversized_history_turn(client):
    history = [{"role": "assistant", "content": "x" * (app_module.MAX_TURN_CHARS + 1)}]
    assert client.post("/chat", json={"message": "hi", "history": history}).status_code == 422


def test_chat_accepts_a_message_at_the_limit(client, graph):
    graph([{"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="ok")}}])
    stream(client, message="x" * app_module.MAX_MESSAGE_CHARS)


# --- streaming --------------------------------------------------------------


def test_tokens_stream_as_individual_records(client, graph):
    graph(
        [
            {"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="Cadre ")}},
            {"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="AI helps.")}},
        ]
    )

    events = stream(client)

    assert events[0] == ("token", json.dumps({"content": "Cadre "}))
    assert json.loads(events[1][1])["content"] == "AI helps."
    assert events[-1] == ("message", "[DONE]")


def test_empty_chunks_are_not_emitted(client, graph):
    graph(
        [
            {"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="")}},
            {"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="x")}},
        ]
    )

    assert [e for e in stream(client) if e[0] == "token"] == [("token", json.dumps({"content": "x"}))]


def test_block_style_content_is_flattened(client, graph):
    graph(
        [
            {
                "event": "on_chat_model_stream",
                "data": {"chunk": AIMessageChunk(content=[{"type": "text", "text": "block"}])},
            }
        ]
    )

    assert json.loads(dict(stream(client))["token"])["content"] == "block"


def test_tool_events_carry_the_name_id_and_sources(client, graph):
    sources = [{"type": "document", "label": "overview.pdf · p.1", "excerpt": "..."}]
    graph(
        [
            {"event": "on_tool_start", "name": "query_knowledge_base", "run_id": "run-1", "data": {}},
            {
                "event": "on_tool_end",
                "name": "query_knowledge_base",
                "run_id": "run-1",
                "data": {
                    "output": ToolMessage(
                        content="chunks", name="query_knowledge_base", tool_call_id="1", artifact=sources
                    )
                },
            },
        ]
    )

    events = dict(stream(client))

    start = json.loads(events["tool_start"])
    assert start == {"tool": "query_knowledge_base", "id": "run-1"}
    end = json.loads(events["tool_end"])
    assert end["tool"] == "query_knowledge_base"
    assert end["id"] == "run-1"
    assert end["sources"] == sources
    # The trailing summary event repeats the full set for the sources panel.
    assert json.loads(events["sources"])["sources"] == sources


def test_a_dict_artifact_contributes_no_sources(client, graph):
    graph(
        [
            {
                "event": "on_tool_end",
                "name": "escalate_to_human",
                "run_id": "run-2",
                "data": {
                    "output": ToolMessage(
                        content="prepared",
                        name="escalate_to_human",
                        tool_call_id="1",
                        artifact={"booking_link": "https://cadreai.com/book", "message": "m"},
                    )
                },
            },
            {"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="ok")}},
        ]
    )

    events = dict(stream(client))

    assert json.loads(events["tool_end"])["sources"] == []
    assert "sources" not in events


def test_a_string_tool_result_does_not_break_the_stream(client, graph):
    graph(
        [
            {"event": "on_tool_end", "name": "query_knowledge_base", "run_id": "r", "data": {"output": "raw"}},
            {"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="ok")}},
        ]
    )

    assert json.loads(dict(stream(client))["tool_end"])["sources"] == []


def test_the_escalate_node_streams_its_templated_message(client, graph):
    graph(
        [
            {
                "event": "on_chain_end",
                "name": "escalate",
                "data": {"output": {"messages": [AIMessage(content="Book here:\n\nhttps://x.test")]}},
            }
        ]
    )

    events = dict(stream(client))

    assert json.loads(events["token"])["content"].startswith("Book here:")


def test_an_empty_run_still_produces_text(client, graph):
    # The client marks a message with no content as failed, so never send none.
    graph([])

    events = dict(stream(client))

    assert "https://cadreai.com/book" in json.loads(events["token"])["content"]
    assert ("message", "[DONE]") in stream(client)


def test_an_exception_becomes_an_error_event_then_done(client, graph):
    graph(
        [{"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="partial")}}],
        error=RuntimeError("openrouter exploded"),
    )

    events = stream(client)

    assert ("token", json.dumps({"content": "partial"})) in events
    assert json.loads(dict(events)["error"])["message"].startswith("The agent hit an error")
    # The internal failure is not leaked to the browser.
    assert "openrouter exploded" not in str(events)
    assert events[-1] == ("message", "[DONE]")


def test_history_is_converted_to_messages(client, graph):
    fake = graph([{"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="ok")}}])

    stream(
        client,
        message="And construction?",
        history=[
            {"role": "user", "content": "Do you serve PE?"},
            {"role": "assistant", "content": "We do."},
            {"role": "agent", "content": "Legacy role name."},
            {"role": "user", "content": "   "},
        ],
    )

    types = [m.type for m in fake.state["messages"]]
    contents = [m.content for m in fake.state["messages"]]
    # Blank turns dropped; `agent` treated as an assistant turn; new message last.
    assert types == ["human", "ai", "ai", "human"]
    assert contents[-1] == "And construction?"
    assert "   " not in contents


def test_the_run_starts_with_a_zeroed_iteration_count(client, graph):
    fake = graph([{"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="ok")}}])

    stream(client)

    assert fake.state["iterations"] == 0


def test_streaming_headers_disable_proxy_buffering(client, graph):
    graph([{"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="ok")}}])

    response = client.post("/chat", json={"message": "hi"})

    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"


def test_a_tool_error_still_closes_the_indicator(client, graph):
    graph(
        [
            {"event": "on_tool_start", "name": "query_knowledge_base", "run_id": "r", "data": {}},
            {"event": "on_tool_error", "name": "query_knowledge_base", "run_id": "r", "data": {}},
            {"event": "on_chat_model_stream", "data": {"chunk": AIMessageChunk(content="ok")}},
        ]
    )

    events = dict(stream(client))

    assert json.loads(events["tool_end"]) == {
        "tool": "query_knowledge_base",
        "id": "r",
        "sources": [],
    }
