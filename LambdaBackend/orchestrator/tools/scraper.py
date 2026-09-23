"""`scrape_cadre_website` — live page content, cached per container."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Literal

import httpx
from bs4 import BeautifulSoup
from langchain_core.tools import tool

from config import get_settings
from orchestrator.prompts import tool_description

logger = logging.getLogger(__name__)

PageName = Literal["services", "about", "industries", "case-studies"]

# The identifier→path map is fixed so the model cannot steer a fetch at an
# arbitrary URL.
PAGE_PATHS: dict[str, str] = {
    "services": "/services",
    "about": "/about",
    "industries": "/industries",
    "case-studies": "/case-studies",
}

# Stripped before text extraction — chrome that would otherwise dominate the
# 4000-character budget with nav links repeated on every page.
_NOISE_TAGS = ("script", "style", "nav", "footer", "header", "noscript", "svg", "form")


@dataclass(frozen=True)
class _Cached:
    expires_at: float
    text: str
    title: str


_CACHE: dict[str, _Cached] = {}


def reset_cache() -> None:
    """Empty the scrape cache. Used by tests."""
    _CACHE.clear()


def page_url(page: str) -> str:
    return f"{get_settings().cadre_website_url}{PAGE_PATHS[page]}"


def clean_html(html: str, max_chars: int) -> tuple[str, str]:
    """Return (plain text, page title) from raw HTML."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(list(_NOISE_TAGS)):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    if not title and soup.h1:
        title = soup.h1.get_text(strip=True)

    text = " ".join(soup.get_text(separator=" ", strip=True).split())
    return text[:max_chars], title


@tool(
    "scrape_cadre_website",
    description=tool_description("scrape_cadre_website"),
    response_format="content_and_artifact",
)
async def scrape_cadre_website(page: PageName) -> tuple[str, list[dict]]:
    """Fetch one page of the Cadre AI website as cleaned plain text."""
    settings = get_settings()

    if page not in PAGE_PATHS:
        return (
            f"Unknown page {page!r}. Choose one of: {', '.join(PAGE_PATHS)}.",
            [],
        )

    url = page_url(page)
    now = time.monotonic()

    cached = _CACHE.get(page)
    if cached and cached.expires_at > now:
        return cached.text, [_source(cached.title, page, url)]

    try:
        async with httpx.AsyncClient(
            timeout=settings.request_timeout, follow_redirects=True
        ) as client:
            response = await client.get(url, headers={"User-Agent": "CadreAI-Chatbot/1.0"})
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning("scrape of %s returned %s", url, exc.response.status_code)
        return (
            f"The {page} page returned HTTP {exc.response.status_code}. Live "
            f"content is unavailable — escalate rather than retrying.",
            [],
        )
    except httpx.HTTPError as exc:
        logger.warning("scrape of %s failed: %s", url, exc)
        return (
            f"Could not reach the {page} page ({exc.__class__.__name__}). Live "
            f"content is unavailable — escalate rather than retrying.",
            [],
        )

    try:
        text, title = clean_html(response.text, settings.scrape_max_chars)
    except Exception as exc:  # noqa: BLE001 - malformed markup must not raise
        logger.warning("could not parse %s: %s", url, exc)
        return (f"The {page} page could not be parsed.", [])

    if not text:
        return (f"The {page} page returned no readable text.", [])

    _CACHE[page] = _Cached(
        expires_at=now + settings.scrape_cache_ttl, text=text, title=title
    )
    return text, [_source(title, page, url)]


def _source(title: str, page: str, url: str) -> dict:
    return {"type": "url", "label": title or f"cadreai.com{PAGE_PATHS[page]}", "url": url}
