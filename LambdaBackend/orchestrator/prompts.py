"""Reads prompts out of `system_prompts.md`.

`backend-plan.md` makes that file the single source of truth for agent
behaviour, so the code parses it rather than restating it. A prompt change is
then a markdown edit reviewed in a diff, with no risk of the file and the code
drifting apart.

The parser is deliberately strict: a heading that has moved or a fence that has
been dropped raises at import time rather than silently shipping an empty
system prompt.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from config import BASE_DIR

PROMPTS_FILE = BASE_DIR / "system_prompts.md"

ESCALATION_REASONS = (
    "pricing",
    "existing_client",
    "custom_scope",
    "user_requested",
    "low_confidence",
)

TOOL_NAMES = ("query_knowledge_base", "scrape_cadre_website", "escalate_to_human")


class PromptError(RuntimeError):
    """Raised when `system_prompts.md` does not have the expected shape."""


@lru_cache(maxsize=1)
def _source() -> str:
    if not PROMPTS_FILE.is_file():
        raise PromptError(f"prompt source not found: {PROMPTS_FILE}")
    return PROMPTS_FILE.read_text(encoding="utf-8")


def _first_fenced_block(heading: str) -> str:
    """The first ``` fenced block after `heading`, up to the next heading."""
    lines = _source().splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        raise PromptError(f"heading not found in system_prompts.md: {heading!r}") from None

    depth = heading.count("#", 0, heading.find(" "))
    collected: list[str] | None = None
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if collected is None:
            # A same-or-higher-level heading means the section ended un-fenced.
            if stripped.startswith("#") and _heading_depth(stripped) <= depth:
                break
            if stripped.startswith("```"):
                collected = []
            continue
        if stripped.startswith("```"):
            return "\n".join(collected).strip()
        collected.append(line)

    raise PromptError(f"no fenced code block under heading {heading!r}")


def _heading_depth(line: str) -> int:
    return len(line) - len(line.lstrip("#"))


def _substitute(text: str) -> str:
    """Fill `{booking_link}` / `{website_url}` from configuration.

    Without this the URLs live in two places — the prompt the model reads and
    the environment the code uses — and they drift. The failure is quiet and
    bad: the escalation template carries the configured link while the model,
    following its system prompt, speaks a different one in the same reply.
    """
    from config import get_settings

    settings = get_settings()
    return text.replace("{booking_link}", settings.booking_link).replace(
        "{website_url}", settings.cadre_website_url
    )


@lru_cache(maxsize=1)
def _orchestrator_template() -> str:
    return _first_fenced_block("## Orchestrator System Prompt")


def orchestrator_system_prompt() -> str:
    return _substitute(_orchestrator_template())


@lru_cache(maxsize=len(TOOL_NAMES))
def _tool_template(tool: str) -> str:
    if tool not in TOOL_NAMES:
        raise PromptError(f"unknown tool {tool!r}")
    return _first_fenced_block(f"### `{tool}`")


def tool_description(tool: str) -> str:
    return _substitute(_tool_template(tool))


@lru_cache(maxsize=1)
def escalation_templates() -> dict[str, str]:
    """The reason-code → message table under `## Escalation Message Templates`."""
    lines = _source().splitlines()
    try:
        start = next(
            i
            for i, line in enumerate(lines)
            if line.strip() == "## Escalation Message Templates"
        )
    except StopIteration:
        raise PromptError("escalation template table not found") from None

    row = re.compile(r"^\|\s*`([a-z_]+)`\s*\|\s*(.+?)\s*\|\s*$")
    templates: dict[str, str] = {}
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if stripped.startswith("##"):
            break
        match = row.match(stripped)
        if match:
            templates[match.group(1)] = match.group(2).strip().strip('"').strip()

    missing = [reason for reason in ESCALATION_REASONS if reason not in templates]
    if missing:
        raise PromptError(f"escalation table is missing reason codes: {missing}")
    return templates


def escalation_message(reason: str) -> str:
    templates = escalation_templates()
    return templates.get(reason, templates["low_confidence"])
