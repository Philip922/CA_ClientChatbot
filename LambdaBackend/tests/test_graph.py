"""Routing decisions and full graph runs against a scripted model."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END

from orchestrator import nodes
from orchestrator.graph import build_graph
from orchestrator.nodes import (
    ESCALATE,
    ORCHESTRATOR,
    TOOL_NODE,
    escalate,
    route_after_orchestrator,
    route_after_tools,
)
from orchestrator.prompts import escalation_templates
from orchestrator.tools import rag


def tool_call(name: str, **args) -> dict:
    return {"name": name, "args": args, "id": f"call-{name}", "type": "tool_call"}


class ScriptedModel:
    """Replays a fixed list of AIMessages, one per orchestrator pass.

    Each pass yields a *fresh* message with its own id, the way a real model
    does. Handing back the same object twice would make `add_messages` treat the
    second turn as an edit of the first.
    """

    def __init__(self, *responses: AIMessage):
        self.responses = list(responses)
        self.calls: list[list] = []

    async def ainvoke(self, messages, **_):
        self.calls.append(messages)
        # The final response repeats, which keeps the unbounded-loop test short.
        template = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        turn = len(self.calls)
        return AIMessage(
            id=f"ai-{turn}",
            content=template.content,
            tool_calls=[
                {**c, "id": f"{c['id']}-{turn}"} for c in (template.tool_calls or [])
            ],
        )


@pytest.fixture
def script(monkeypatch):
    def install(*responses: AIMessage) -> ScriptedModel:
        model = ScriptedModel(*responses)
        monkeypatch.setattr(nodes, "get_llm", lambda: model)
        return model

    return install


@pytest.fixture
def wired_index(monkeypatch, stub_index, stub_embedder, request):
    monkeypatch.setattr(rag, "_index", stub_index)
    monkeypatch.setattr(rag, "_embedder", stub_embedder)
    monkeypatch.setenv("RAG_MIN_SCORE", "0.1")
    from config import get_settings

    get_settings.cache_clear()
    return stub_index


# --- routing after the orchestrator ----------------------------------------


def test_a_final_answer_ends_the_run():
    state = {"messages": [AIMessage(content="Here is the answer.")], "iterations": 1}
    assert route_after_orchestrator(state) == END


def test_a_tool_call_goes_to_the_tool_node():
    state = {
        "messages": [AIMessage(content="", tool_calls=[tool_call("query_knowledge_base", query="x")])],
        "iterations": 1,
    }
    assert route_after_orchestrator(state) == TOOL_NODE


def test_the_iteration_cap_forces_escalation():
    state = {
        "messages": [AIMessage(content="", tool_calls=[tool_call("query_knowledge_base", query="x")])],
        "iterations": 3,
    }
    assert route_after_orchestrator(state) == ESCALATE


def test_a_final_answer_on_the_last_iteration_still_wins():
    state = {"messages": [AIMessage(content="Answered.")], "iterations": 3}
    assert route_after_orchestrator(state) == END


def test_an_escalation_call_runs_the_tool_even_at_the_cap():
    state = {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[tool_call("escalate_to_human", reason="pricing", conversation_summary="s")],
            )
        ],
        "iterations": 3,
    }
    # The tool owns the template, so it runs rather than being short-circuited.
    assert route_after_orchestrator(state) == TOOL_NODE


def test_an_empty_message_list_ends_the_run():
    assert route_after_orchestrator({"messages": [], "iterations": 0}) == END


# --- routing after the tools ------------------------------------------------


def test_a_knowledge_base_result_returns_to_the_model():
    state = {
        "messages": [ToolMessage(content="chunks", name="query_knowledge_base", tool_call_id="1")],
        "iterations": 1,
    }
    assert route_after_tools(state) == ORCHESTRATOR


def test_an_escalation_result_goes_to_the_escalate_node():
    state = {
        "messages": [ToolMessage(content="prepared", name="escalate_to_human", tool_call_id="1")],
        "iterations": 1,
    }
    assert route_after_tools(state) == ESCALATE


def test_an_escalation_among_parallel_results_still_escalates():
    state = {
        "messages": [
            ToolMessage(content="chunks", name="query_knowledge_base", tool_call_id="1"),
            ToolMessage(content="prepared", name="escalate_to_human", tool_call_id="2"),
        ],
        "iterations": 1,
    }
    assert route_after_tools(state) == ESCALATE


def test_the_cap_escalates_rather_than_calling_the_model_again():
    state = {
        "messages": [ToolMessage(content="chunks", name="query_knowledge_base", tool_call_id="1")],
        "iterations": 3,
    }
    assert route_after_tools(state) == ESCALATE


# --- the escalate node ------------------------------------------------------


def test_escalate_reuses_the_tool_artifact():
    artifact = {
        "reason": "pricing",
        "booking_link": "https://cadreai.com/book",
        "message": escalation_templates()["pricing"],
        "summary": "Asked about cost.",
    }
    state = {
        "messages": [
            ToolMessage(
                content="prepared", name="escalate_to_human", tool_call_id="1", artifact=artifact
            )
        ],
        "iterations": 1,
    }

    result = escalate(state)

    assert result["escalation_reason"] == "pricing"
    assert escalation_templates()["pricing"] in result["messages"][0].content
    assert "https://cadreai.com/book" in result["messages"][0].content


def test_escalate_falls_back_to_low_confidence_at_the_cap():
    state = {"messages": [HumanMessage(content="What does a rollout cost?")], "iterations": 3}

    result = escalate(state)

    assert result["escalation_reason"] == "low_confidence"
    assert escalation_templates()["low_confidence"] in result["messages"][0].content


def test_escalate_ignores_a_malformed_artifact():
    state = {
        "messages": [
            HumanMessage(content="hi"),
            ToolMessage(content="prepared", name="escalate_to_human", tool_call_id="1", artifact="junk"),
        ],
        "iterations": 1,
    }

    assert escalate(state)["escalation_reason"] == "low_confidence"


# --- full graph runs --------------------------------------------------------


async def test_the_model_can_answer_without_a_tool(script):
    model = script(AIMessage(content="Cadre AI is an AI consultancy."))

    result = await build_graph().ainvoke({"messages": [HumanMessage(content="Who are you?")], "iterations": 0})

    assert result["messages"][-1].content == "Cadre AI is an AI consultancy."
    assert result["iterations"] == 1
    assert len(model.calls) == 1


async def test_a_knowledge_base_lookup_then_an_answer(script, wired_index):
    script(
        AIMessage(content="", tool_calls=[tool_call("query_knowledge_base", query="which industries")]),
        AIMessage(content="Private equity and construction, among others."),
    )

    result = await build_graph().ainvoke(
        {"messages": [HumanMessage(content="What industries?")], "iterations": 0}
    )

    tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert [m.name for m in tool_messages] == ["query_knowledge_base"]
    assert "overview.pdf" in tool_messages[0].content
    assert result["messages"][-1].content.startswith("Private equity")
    assert result["iterations"] == 2


async def test_a_model_driven_escalation_ends_with_the_template(script):
    script(
        AIMessage(
            content="",
            tool_calls=[
                tool_call("escalate_to_human", reason="pricing", conversation_summary="Asked about cost.")
            ],
        )
    )

    result = await build_graph().ainvoke(
        {"messages": [HumanMessage(content="How much does it cost?")], "iterations": 0}
    )

    assert result["escalation_reason"] == "pricing"
    final = result["messages"][-1].content
    assert escalation_templates()["pricing"] in final
    assert "https://cadreai.com/book" in final


async def test_the_cap_stops_the_loop_after_max_iterations(script, wired_index):
    # A model that only ever wants another lookup — the cap is what ends it.
    model = script(AIMessage(content="", tool_calls=[tool_call("query_knowledge_base", query="loop")]))

    result = await build_graph().ainvoke(
        {"messages": [HumanMessage(content="Tell me everything.")], "iterations": 0}
    )

    assert result["iterations"] == 3
    assert len(model.calls) == 3
    assert result["escalation_reason"] == "low_confidence"
    assert escalation_templates()["low_confidence"] in result["messages"][-1].content


async def test_a_lower_cap_is_honoured(script, wired_index, monkeypatch):
    monkeypatch.setenv("MAX_ITERATIONS", "1")
    from config import get_settings

    get_settings.cache_clear()
    model = script(AIMessage(content="", tool_calls=[tool_call("query_knowledge_base", query="loop")]))

    result = await build_graph().ainvoke(
        {"messages": [HumanMessage(content="Tell me everything.")], "iterations": 0}
    )

    assert len(model.calls) == 1
    assert result["escalation_reason"] == "low_confidence"


async def test_history_reaches_the_model(script):
    model = script(AIMessage(content="Yes, construction too."))

    await build_graph().ainvoke(
        {
            "messages": [
                HumanMessage(content="Do you serve private equity?"),
                AIMessage(content="We do."),
                HumanMessage(content="What about construction?"),
            ],
            "iterations": 0,
        }
    )

    # System prompt, then the three history turns.
    sent = model.calls[0]
    assert sent[0].type == "system"
    assert [m.content for m in sent[1:]] == [
        "Do you serve private equity?",
        "We do.",
        "What about construction?",
    ]
