"""The `system_prompts.md` parser.

Load-bearing: if parsing silently returned an empty string the agent would run
with no system prompt and no tool descriptions, which is exactly the failure
that is hardest to notice in a response.
"""

from __future__ import annotations

import pytest

from orchestrator import prompts
from orchestrator.prompts import (
    ESCALATION_REASONS,
    TOOL_NAMES,
    PromptError,
    escalation_message,
    escalation_templates,
    orchestrator_system_prompt,
    tool_description,
)


def test_the_system_prompt_is_the_fenced_block():
    text = orchestrator_system_prompt()

    assert text.startswith("You are the Cadre AI support assistant")
    assert "ANSWER RULES" in text
    assert "https://cadreai.com/book" in text
    # The fence markers and the surrounding prose must not leak in.
    assert "```" not in text
    assert "This prompt is injected" not in text


def test_the_system_prompt_names_every_tool():
    text = orchestrator_system_prompt()
    for name in TOOL_NAMES:
        assert name in text


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_tool_has_a_description(name):
    description = tool_description(name)

    assert len(description) > 100
    assert "```" not in description


def test_tool_descriptions_are_distinct():
    descriptions = {tool_description(name) for name in TOOL_NAMES}
    assert len(descriptions) == len(TOOL_NAMES)


def test_an_unknown_tool_raises():
    with pytest.raises(PromptError, match="unknown tool"):
        tool_description("query_the_void")


def test_every_escalation_reason_has_a_template():
    templates = escalation_templates()

    assert set(templates) >= set(ESCALATION_REASONS)
    for reason in ESCALATION_REASONS:
        assert templates[reason]
        # The surrounding quotes are framing in the markdown table, not content.
        assert not templates[reason].startswith('"')


def test_an_unknown_reason_falls_back_to_low_confidence():
    assert escalation_message("nonsense") == escalation_templates()["low_confidence"]


def test_a_missing_heading_raises(monkeypatch):
    monkeypatch.setattr(prompts, "_source", lambda: "# Nothing here\n")
    prompts._orchestrator_template.cache_clear()

    with pytest.raises(PromptError, match="heading not found"):
        orchestrator_system_prompt()

    prompts._orchestrator_template.cache_clear()


def test_a_heading_without_a_fence_raises(monkeypatch):
    monkeypatch.setattr(
        prompts, "_source", lambda: "## Orchestrator System Prompt\n\nJust prose.\n\n## Next\n"
    )
    prompts._orchestrator_template.cache_clear()

    with pytest.raises(PromptError, match="no fenced code block"):
        orchestrator_system_prompt()

    prompts._orchestrator_template.cache_clear()


def test_an_incomplete_escalation_table_raises(monkeypatch):
    monkeypatch.setattr(
        prompts,
        "_source",
        lambda: "## Escalation Message Templates\n\n| `pricing` | Only one row |\n",
    )
    escalation_templates.cache_clear()

    with pytest.raises(PromptError, match="missing reason codes"):
        escalation_templates()

    escalation_templates.cache_clear()


def test_the_system_prompt_uses_the_configured_booking_link(monkeypatch):
    monkeypatch.setenv("BOOKING_LINK", "https://cadre.ai/contact")
    from config import get_settings

    get_settings.cache_clear()

    text = orchestrator_system_prompt()

    assert "https://cadre.ai/contact" in text
    assert "{booking_link}" not in text


def test_the_scraper_description_uses_the_configured_site(monkeypatch):
    monkeypatch.setenv("CADRE_WEBSITE_URL", "https://cadre.ai")
    from config import get_settings

    get_settings.cache_clear()

    text = tool_description("scrape_cadre_website")

    assert "https://cadre.ai/services" in text
    assert "{website_url}" not in text


def test_no_placeholder_survives_substitution():
    # A typo'd placeholder would otherwise reach the model verbatim.
    texts = [orchestrator_system_prompt(), *(tool_description(n) for n in TOOL_NAMES)]
    for text in texts:
        assert "{booking_link}" not in text
        assert "{website_url}" not in text


def test_the_prompt_tracks_a_booking_link_change_within_a_process(monkeypatch):
    # Caching the parse must not cache the substitution.
    from config import get_settings

    monkeypatch.setenv("BOOKING_LINK", "https://first.test/book")
    get_settings.cache_clear()
    assert "https://first.test/book" in orchestrator_system_prompt()

    monkeypatch.setenv("BOOKING_LINK", "https://second.test/book")
    get_settings.cache_clear()
    assert "https://second.test/book" in orchestrator_system_prompt()
