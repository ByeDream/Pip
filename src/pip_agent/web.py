"""Local ``web_fetch`` implementation backing ``mcp__pip__web_fetch``.

Exposed as the canonical ``web_fetch`` tool for the agent — Claude Code's
own ``WebFetch`` is shadowed via ``disallowed_tools`` (see
:mod:`pip_agent.agent_runner`) so the agent only ever reaches for one
implementation. This module is a thin async wrapper around
:mod:`httpx` + :mod:`trafilatura`:

* HTTP GET with redirects, a 30 s timeout, and a 5 MB hard response cap.
* HTML / XHTML responses are reduced to article-body markdown via
  :func:`trafilatura.extract`. JSON / plain-text / other text content
  types are returned verbatim. Binary content types are refused.
* Errors (timeout, non-2xx, oversized, transport failure) are returned
  as ``{"ok": False, "error": ...}`` rather than raised — callers
  surface the string to the model instead of crashing the turn.

Wrapped in :func:`pip_agent._profile.span` so every fetch shows up as a
``web.fetch`` row in profile traces.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


_DEFAULT_TIMEOUT_S: float = 30.0
# Response body cap (bytes). 5 MB covers article-shaped pages with
# generous headroom while still rejecting accidental hits on large
# binaries (PDFs, images, archives) that would balloon the model's
# context if extraction silently passed them through.
_MAX_RESPONSE_BYTES: int = 5 * 1024 * 1024

# Default character cap for the *returned* extracted content. Callers
# can override per-call via ``max_chars``. Picked to fit comfortably
# inside a single tool result without dominating the model's context
# — long pages still come back with the head intact and a
# ``truncated`` flag so the model knows to ask for less or follow up.
_DEFAULT_MAX_CHARS: int = 50_000

# A real-browser-ish UA. A handful of CDNs (Cloudflare's challenge
# pages, some e-commerce front-ends) hard-block the bare
# ``python-httpx`` UA; we are not trying to evade bot detection, just
# to look like a normal HTTP client so plain article pages work.
_DEFAULT_USER_AGENT: str = (
    "Mozilla/5.0 (compatible; Pip-Boy/1.0; "
    "+https://github.com/ByeDream/Pip-Boy)"
)

# Content types that round-trip as-is (the model reads them directly,
# no extraction needed). Anything else falls through to either the
# HTML-extract branch or the binary-refuse branch.
_PASSTHROUGH_PREFIXES: tuple[str, ...] = (
    "application/json",
    "application/ld+json",
    "application/xml",
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/xml",
    "text/x-",
)
_HTML_TYPES: tuple[str, ...] = (
    "text/html",
    "application/xhtml+xml",
)


async def fetch_url(
    url: str,
    *,
    max_chars: int = _DEFAULT_MAX_CHARS,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """Fetch ``url`` and return either extracted text or an error dict.

    Parameters
    ----------
    url:
        Absolute http(s) URL to GET.
    max_chars:
        Maximum characters of the *returned* content string. The full
        body is always downloaded and counted against
        :data:`_MAX_RESPONSE_BYTES`; ``max_chars`` only trims the
        post-extraction text the model sees.
    timeout:
        Total request timeout in seconds. Includes connect, read, and
        write phases — matches httpx's default timeout semantics.

    Returns
    -------
    dict
        Success::

            {
                "ok": True,
                "url": <final-url-after-redirects>,
                "status": <int>,
                "content_type": <str>,
                "content": <str>,
                "truncated": <bool>,
            }

        Failure::

            {
                "ok": False,
                "error": <str>,
                "url": <url>,
                "status": <int | None>,
            }
    """
    from pip_agent import _profile  # PROFILE

    async with _profile.span("web.fetch", url=url):
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=timeout,
                headers={"User-Agent": _DEFAULT_USER_AGENT},
            ) as client:
                resp = await client.get(url)
        except httpx.TimeoutException as exc:
            return {
                "ok": False,
                "error": f"timeout after {timeout}s: {exc}",
                "url": url,
                "status": None,
            }
        except httpx.HTTPError as exc:
            return {
                "ok": False,
                "error": f"http error: {exc}",
                "url": url,
                "status": None,
            }
        except Exception as exc:  # noqa: BLE001
            # Defensive: httpx wraps most failures in HTTPError, but
            # we don't want a stray DNS/SSL/proxy edge case to escape
            # as an unhandled exception that crashes the agent turn.
            return {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "url": url,
                "status": None,
            }

        final_url = str(resp.url)
        status = resp.status_code
        content_type = (
            resp.headers.get("content-type", "")
            .split(";", 1)[0]
            .strip()
            .lower()
        )

        if status >= 400:
            return {
                "ok": False,
                "error": f"HTTP {status}",
                "url": final_url,
                "status": status,
            }

        body_bytes = resp.content
        if len(body_bytes) > _MAX_RESPONSE_BYTES:
            return {
                "ok": False,
                "error": (
                    f"response too large ({len(body_bytes)} bytes > "
                    f"{_MAX_RESPONSE_BYTES} cap)"
                ),
                "url": final_url,
                "status": status,
            }

        content = _select_content(content_type, resp.text)
        if content is None:
            return {
                "ok": False,
                "error": (
                    f"unsupported content type {content_type!r} "
                    "(non-text payload)"
                ),
                "url": final_url,
                "status": status,
            }

        truncated = False
        if len(content) > max_chars:
            content = content[:max_chars]
            truncated = True

        return {
            "ok": True,
            "url": final_url,
            "status": status,
            "content_type": content_type,
            "content": content,
            "truncated": truncated,
        }


def _select_content(content_type: str, text: str) -> str | None:
    """Pick the right rendering for a given Content-Type.

    Returns ``None`` for content types we won't pass to the model
    (binaries, images, archives) — the caller turns this into an
    error response. HTML is sent through trafilatura; passthrough
    types come back verbatim. Anything else that *looks* textual
    (empty content type, ``text/*`` not in our allowlist) is also
    passed through verbatim — refusing it would be paternalistic.
    """
    if content_type in _HTML_TYPES:
        extracted = _extract_html(text)
        # Extraction can return None on pages with no detectable main
        # content (login walls, JS-only SPAs). Falling back to the raw
        # response is noisy but better than a hard refusal — the model
        # can decide what to do with it.
        return extracted if extracted else text

    if any(content_type.startswith(p) for p in _PASSTHROUGH_PREFIXES):
        return text

    # Empty / unknown content type but the body is already a string
    # (httpx decoded it as text per its own heuristic) → trust the
    # body and pass it through. This covers servers that omit the
    # header or send something idiosyncratic.
    if not content_type or content_type.startswith("text/"):
        return text

    return None


def _extract_html(html: str) -> str | None:
    """Reduce ``html`` to article-body markdown via trafilatura.

    Returns ``None`` when extraction yields nothing usable — caller
    decides whether to fall back to the raw HTML string or surface
    an error.
    """
    try:
        import trafilatura
    except ImportError:
        log.warning("trafilatura not installed; HTML extraction disabled")
        return None

    try:
        return trafilatura.extract(
            html,
            output_format="markdown",
            include_links=True,
            include_images=False,
            include_tables=True,
            favor_recall=True,
        )
    except Exception as exc:  # noqa: BLE001
        # trafilatura is generally robust but lxml occasionally chokes
        # on adversarial HTML; never let an extraction crash kill the
        # turn — fall back to the raw response.
        log.warning("trafilatura.extract failed: %s", exc)
        return None
