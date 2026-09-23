"""The three tools, exercised the way ToolNode invokes them.

Each tool is called with a `tool_call` dict so the result is a `ToolMessage` —
that is what the graph passes around, and it carries the `artifact` the SSE
layer reads for citations.
"""

from __future__ import annotations

import itertools

import pytest
import respx
from httpx import Response, TimeoutException

from orchestrator.prompts import ESCALATION_REASONS, escalation_templates
from orchestrator.tools import rag, scraper
from orchestrator.tools.escalation import build_handoff, escalate_to_human
from orchestrator.tools.rag import query_knowledge_base
from orchestrator.tools.scraper import PAGE_PATHS, clean_html, scrape_cadre_website

_ids = itertools.count()


async def call(tool, **args):
    """Invoke a tool as ToolNode does, returning the ToolMessage."""
    return await tool.ainvoke(
        {"name": tool.name, "args": args, "id": f"call-{next(_ids)}", "type": "tool_call"}
    )


# --- query_knowledge_base ---------------------------------------------------


@pytest.fixture
def wired_index(monkeypatch, stub_index, stub_embedder):
    """Point the tool at the stub index instead of a file on disk."""
    monkeypatch.setattr(rag, "_index", stub_index)
    monkeypatch.setattr(rag, "_embedder", stub_embedder)
    return stub_index


async def test_kb_returns_chunks_and_document_sources(wired_index, monkeypatch):
    monkeypatch.setenv("RAG_MIN_SCORE", "0.1")
    from config import get_settings

    get_settings.cache_clear()

    message = await call(query_knowledge_base, query="which industries")

    assert "overview.pdf p.1" in message.content
    assert "score" in message.content
    assert message.artifact
    source = message.artifact[0]
    assert source["type"] == "document"
    assert source["label"] == "overview.pdf · p.1"
    assert "industries" in source["excerpt"].lower()


async def test_kb_reports_nothing_above_the_threshold(wired_index):
    message = await call(query_knowledge_base, query="completely unrelated")

    assert "threshold" in message.content
    assert message.artifact == []


async def test_kb_respects_an_explicit_top_k(wired_index, monkeypatch):
    monkeypatch.setenv("RAG_MIN_SCORE", "0.0")
    from config import get_settings

    get_settings.cache_clear()

    message = await call(query_knowledge_base, query="industries strategy agents", top_k=1)

    assert len(message.artifact) == 1


async def test_kb_falls_back_to_the_configured_top_k(wired_index, monkeypatch):
    monkeypatch.setenv("RAG_MIN_SCORE", "0.0")
    monkeypatch.setenv("RAG_TOP_K", "2")
    from config import get_settings

    get_settings.cache_clear()

    message = await call(query_knowledge_base, query="industries strategy agents", top_k=0)

    assert len(message.artifact) == 2


async def test_kb_degrades_to_a_string_when_the_index_is_missing(monkeypatch, tmp_path):
    monkeypatch.setenv("INDEX_PATH", str(tmp_path / "absent.npz"))
    from config import get_settings

    get_settings.cache_clear()

    message = await call(query_knowledge_base, query="anything")

    # Must not raise: the prompt tells the model to scrape when the KB is down.
    assert "unavailable" in message.content
    assert "scrape_cadre_website" in message.content
    assert message.artifact == []


# --- scrape_cadre_website ---------------------------------------------------

_HTML = """
<html><head><title>Cadre AI — Services</title></head>
<body>
  <nav>Home Services About Contact</nav>
  <script>window.analytics = 1;</script>
  <style>body { color: red; }</style>
  <main><h1>Services</h1><p>AI Strategy and AI Engineering engagements.</p></main>
  <footer>© Cadre AI</footer>
</body></html>
"""


def test_clean_html_strips_chrome_and_keeps_body_text():
    text, title = clean_html(_HTML, max_chars=4000)

    assert "AI Strategy and AI Engineering engagements." in text
    assert "window.analytics" not in text
    assert "color: red" not in text
    assert "Contact" not in text
    assert title == "Cadre AI — Services"


def test_clean_html_caps_the_text():
    text, _ = clean_html("<p>" + ("word " * 5000) + "</p>", max_chars=100)
    assert len(text) == 100


def test_clean_html_falls_back_to_the_h1_for_a_title():
    _, title = clean_html("<html><body><h1>Industries</h1></body></html>", 4000)
    assert title == "Industries"


@respx.mock
async def test_scraper_returns_text_and_a_url_source():
    route = respx.get("https://cadreai.test/services").mock(
        return_value=Response(200, html=_HTML)
    )

    message = await call(scrape_cadre_website, page="services")

    assert route.called
    assert "AI Engineering" in message.content
    assert message.artifact == [
        {
            "type": "url",
            "label": "Cadre AI — Services",
            "url": "https://cadreai.test/services",
        }
    ]


@respx.mock
async def test_scraper_serves_the_second_call_from_cache():
    route = respx.get("https://cadreai.test/about").mock(
        return_value=Response(200, html=_HTML)
    )

    first = await call(scrape_cadre_website, page="about")
    second = await call(scrape_cadre_website, page="about")

    assert route.call_count == 1
    assert first.content == second.content


@respx.mock
async def test_scraper_refetches_once_the_ttl_expires(monkeypatch):
    monkeypatch.setenv("SCRAPE_CACHE_TTL", "0")
    from config import get_settings

    get_settings.cache_clear()
    route = respx.get("https://cadreai.test/about").mock(
        return_value=Response(200, html=_HTML)
    )

    await call(scrape_cadre_website, page="about")
    await call(scrape_cadre_website, page="about")

    assert route.call_count == 2


@respx.mock
async def test_scraper_caches_per_page_not_globally():
    services = respx.get("https://cadreai.test/services").mock(
        return_value=Response(200, html=_HTML)
    )
    industries = respx.get("https://cadreai.test/industries").mock(
        return_value=Response(200, html=_HTML)
    )

    await call(scrape_cadre_website, page="services")
    await call(scrape_cadre_website, page="industries")

    assert services.called and industries.called


@respx.mock
async def test_scraper_describes_a_404_without_raising():
    respx.get("https://cadreai.test/case-studies").mock(return_value=Response(404))

    message = await call(scrape_cadre_website, page="case-studies")

    assert "HTTP 404" in message.content
    assert "escalate" in message.content
    assert message.artifact == []


@respx.mock
async def test_scraper_describes_a_timeout_without_raising():
    respx.get("https://cadreai.test/services").mock(side_effect=TimeoutException("slow"))

    message = await call(scrape_cadre_website, page="services")

    assert "Could not reach" in message.content
    assert message.artifact == []


@respx.mock
async def test_scraper_does_not_cache_a_failure():
    route = respx.get("https://cadreai.test/services")
    route.mock(return_value=Response(500))
    await call(scrape_cadre_website, page="services")

    route.mock(return_value=Response(200, html=_HTML))
    message = await call(scrape_cadre_website, page="services")

    assert "AI Engineering" in message.content


@respx.mock
async def test_scraper_reports_an_empty_page():
    respx.get("https://cadreai.test/about").mock(
        return_value=Response(200, html="<html><body><script>x</script></body></html>")
    )

    message = await call(scrape_cadre_website, page="about")

    assert "no readable text" in message.content


def test_every_page_identifier_maps_to_a_path():
    assert set(PAGE_PATHS) == {"services", "about", "industries", "case-studies"}


# --- escalate_to_human ------------------------------------------------------


@pytest.mark.parametrize("reason", ESCALATION_REASONS)
async def test_escalation_maps_every_reason_to_its_template(reason):
    message = await call(
        escalate_to_human, reason=reason, conversation_summary="User asked about X."
    )

    payload = message.artifact
    assert payload["reason"] == reason
    assert payload["message"] == escalation_templates()[reason]
    assert payload["booking_link"] == "https://cadreai.com/book"
    assert payload["summary"] == "User asked about X."
    # The content is what the model reads, so the link has to be in it too.
    assert "https://cadreai.com/book" in message.content


def test_build_handoff_falls_back_on_an_unknown_reason():
    payload = build_handoff("something_else", "")
    assert payload["reason"] == "low_confidence"


def test_build_handoff_uses_the_configured_booking_link(monkeypatch):
    monkeypatch.setenv("BOOKING_LINK", "https://example.test/talk")
    from config import get_settings

    get_settings.cache_clear()

    assert build_handoff("pricing", "")["booking_link"] == "https://example.test/talk"


async def test_kb_degrades_when_the_index_file_is_corrupt(monkeypatch, tmp_path):
    # numpy raises ValueError here, not a RuntimeError — the tool must still
    # answer with a string so the model can fall back to scraping.
    bad = tmp_path / "corrupt.npz"
    bad.write_text("not an npz archive")
    monkeypatch.setenv("INDEX_PATH", str(bad))
    from config import get_settings

    get_settings.cache_clear()

    message = await call(query_knowledge_base, query="anything")

    assert "unavailable" in message.content
    assert message.artifact == []


@respx.mock
async def test_scraper_survives_unparseable_markup(monkeypatch):
    respx.get("https://cadreai.test/services").mock(
        return_value=Response(200, html="<html><body>ok</body></html>")
    )
    monkeypatch.setattr(
        scraper, "clean_html", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
    )

    message = await call(scrape_cadre_website, page="services")

    assert "could not be parsed" in message.content
