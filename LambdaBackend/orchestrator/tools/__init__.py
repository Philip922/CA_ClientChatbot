"""The three tools the orchestrator can call.

Each is declared with `response_format="content_and_artifact"`: the content
string is what the LLM reads, the artifact is structured source data that rides
along on the `ToolMessage` for the SSE `tool_end` event. Keeping them separate
means the UI gets citations without spending context on JSON.
"""

from orchestrator.tools.escalation import escalate_to_human
from orchestrator.tools.rag import query_knowledge_base
from orchestrator.tools.scraper import scrape_cadre_website

TOOLS = [query_knowledge_base, scrape_cadre_website, escalate_to_human]

__all__ = [
    "TOOLS",
    "query_knowledge_base",
    "scrape_cadre_website",
    "escalate_to_human",
]
