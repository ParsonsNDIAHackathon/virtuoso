"""Server-side source preview for the dashboard's source viewer.

Why this exists: the viewer used to render every non-Telegram link in a generic
``<iframe>``. Most news providers send ``X-Frame-Options: DENY/SAMEORIGIN`` or a
``Content-Security-Policy: frame-ancestors`` header, so Firefox shows
"Firefox can't open this page" instead of the article. Telegram was the only
source that worked because it uses Telegram's official embed widget, not an
iframe. No client-side trick can override the provider's framing policy, so the
engine fetches the page itself and returns a same-origin summary (title,
description, lead image, article text) that the viewer renders inline.

Only stdlib HTML parsing is used (no extra dependencies). Fetching is
guarded: http(s) only, no credentials, default ports only, DNS is resolved and
private/loopback/link-local targets are rejected (SSRF protection), redirects
are followed manually with re-validation, responses are capped at 2 MB with a
10 s timeout. Results are cached in memory for one hour.
"""
from __future__ import annotations

import html as _html
import ipaddress
import logging
import socket
import time
from collections import OrderedDict
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import requests

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (ParsonsOfInterest-MultiINT hackathon; +source-preview)"}
TIMEOUT_S = 10
MAX_BYTES = 2_000_000
MAX_REDIRECTS = 5
MAX_PARAGRAPHS = 8
MAX_TEXT_CHARS = 4000
CACHE_TTL_S = 3600
CACHE_MAX = 500

_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()


class PreviewError(ValueError):
    """Invalid preview URL (mapped to HTTP 422 by the API layer)."""


# ---------------------------------------------------------------------------
# URL validation (SSRF guard)
# ---------------------------------------------------------------------------

def _check_host(host: str, port: int | None) -> None:
    """Reject non-public redirect/fetch targets. Raises PreviewError."""
    if port is not None and port not in (80, 443):
        raise PreviewError(f"refusing non-default port {port}")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        raise PreviewError(f"cannot resolve host {host!r}") from None
    if not infos:
        raise PreviewError(f"cannot resolve host {host!r}")
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            raise PreviewError(f"cannot resolve host {host!r}") from None
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast
                or ip.is_reserved or ip.is_unspecified):
            raise PreviewError(f"refusing non-public address for {host!r}")


def validate_url(url: str) -> str:
    """Return a normalized URL or raise PreviewError."""
    if not url or len(url) > 2000:
        raise PreviewError("url is missing or too long")
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise PreviewError("only http(s) URLs can be previewed")
    if not parsed.hostname:
        raise PreviewError("url has no host")
    if parsed.username or parsed.password:
        raise PreviewError("urls with credentials cannot be previewed")
    _check_host(parsed.hostname, parsed.port)
    return parsed.geturl()


# ---------------------------------------------------------------------------
# HTML extraction (stdlib only)
# ---------------------------------------------------------------------------

_SKIP_TAGS = {"script", "style", "noscript", "template"}
_TEXT_TAGS = {"p", "h1", "h2", "h3", "li"}


class _ArticleParser(HTMLParser):
    """Collect title, meta/og tags and visible text blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        self._in_title = False
        self.meta: dict[str, str] = {}
        self.blocks: list[str] = []
        self._current_tag: str | None = None
        self._buf: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            d = {k.lower(): (v or "") for k, v in attrs}
            key = (d.get("property") or d.get("name") or "").lower()
            if key and "content" in d:
                self.meta.setdefault(key, d["content"].strip())
        elif tag in _TEXT_TAGS:
            self._flush()
            self._current_tag = tag

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        elif tag in _TEXT_TAGS:
            self._flush()
            self._current_tag = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title = ((self.title or "") + data).strip()
        elif self._current_tag:
            self._buf.append(data)

    def _flush(self) -> None:
        text = _html.unescape(" ".join("".join(self._buf).split())).strip()
        self._buf = []
        if text:
            self.blocks.append(text)


def extract_preview(page_html: str, base_url: str) -> dict:
    """Pure extraction: HTML + page URL -> title/description/image/text fields."""
    parser = _ArticleParser()
    try:
        parser.feed(page_html)
    except Exception as e:  # never let a malformed page break the endpoint
        log.debug("preview parse failed for %s: %s", base_url, e)
    meta = parser.meta
    title = (meta.get("og:title") or meta.get("twitter:title") or parser.title or None)
    if title:
        title = _html.unescape(" ".join(title.split()))[:300]
    description = (meta.get("og:description") or meta.get("twitter:description")
                   or meta.get("description") or None)
    if description:
        description = _html.unescape(" ".join(description.split()))[:600]
    image = meta.get("og:image") or meta.get("twitter:image")
    if image:
        image = urljoin(base_url, image)
    paras = [b for b in parser.blocks if len(b) >= 40][:MAX_PARAGRAPHS]
    if not paras:  # thin pages (JS shells): keep the longest blocks as a hint
        paras = sorted(parser.blocks, key=len, reverse=True)[:2]
    text = "\n\n".join(paras)[:MAX_TEXT_CHARS]
    return {"title": title, "description": description, "image": image or None, "text": text}


# ---------------------------------------------------------------------------
# Fetch + cache
# ---------------------------------------------------------------------------

def _fetch(url: str) -> tuple[str, str, dict]:
    """GET with manual redirect validation. Returns (html, final_url, headers)."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        resp = requests.get(current, headers=HEADERS, timeout=TIMEOUT_S,
                            stream=True, allow_redirects=False)
        status = resp.status_code
        if status in (301, 302, 303, 307, 308):
            location = (resp.headers.get("location") or "").strip()
            if not location:
                raise PreviewError("redirect without a location")
            current = urljoin(current, location)
            validate_url(current)  # re-validate every hop
            continue
        if status >= 400:
            raise PreviewError(f"page returned HTTP {status}")
        ctype = (resp.headers.get("content-type") or "").lower()
        if "html" not in ctype and "xhtml" not in ctype:
            if ctype.startswith("image/"):
                return "", current, {"image_only": True, **resp.headers}
            raise PreviewError(f"unsupported content type {ctype or 'unknown'}")
        chunks: list[bytes] = []
        size = 0
        for chunk in resp.iter_content(65536):
            if not chunk:
                continue
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_BYTES:
                break
        raw = b"".join(chunks)[:MAX_BYTES]
        encoding = resp.encoding or "utf-8"
        try:
            return raw.decode(encoding, errors="replace"), current, resp.headers
        except LookupError:
            return raw.decode("utf-8", errors="replace"), current, resp.headers
    raise PreviewError("too many redirects")


def preview_url(url: str) -> dict:
    """Full pipeline: validate -> cached fetch -> extract. Never raises except
    PreviewError for invalid URLs; fetch failures are returned as an error
    payload so the viewer can fall back gracefully."""
    normalized = validate_url(url)
    now = time.time()
    hit = _cache.get(normalized)
    if hit and hit[0] > now:
        _cache.move_to_end(normalized)
        return hit[1]
    try:
        page_html, final_url, headers = _fetch(normalized)
    except PreviewError as e:  # valid URL, bad page (404, redirect loop, PDF…)
        log.info("preview unavailable for %s: %s", normalized, e)
        return {"url": normalized, "final_url": normalized,
                "site": (urlparse(normalized).hostname or ""),
                "title": None, "description": None, "image": None, "text": "",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "error": str(e)}
    except Exception as e:
        log.warning("preview fetch failed for %s: %s", normalized, str(e)[:160])
        return {"url": normalized, "final_url": normalized,
                "site": (urlparse(normalized).hostname or ""),
                "title": None, "description": None, "image": None, "text": "",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "error": "page could not be retrieved"}
    if headers.get("image_only"):
        payload = {"url": normalized, "final_url": final_url,
                   "site": (urlparse(final_url).hostname or ""),
                   "title": None, "description": None, "image": final_url, "text": "",
                   "fetched_at": datetime.now(timezone.utc).isoformat(), "error": None}
    else:
        extracted = extract_preview(page_html, final_url)
        payload = {"url": normalized, "final_url": final_url,
                   "site": (urlparse(final_url).hostname or ""), **extracted,
                   "fetched_at": datetime.now(timezone.utc).isoformat(), "error": None}
    _cache[normalized] = (now + CACHE_TTL_S, payload)
    _cache.move_to_end(normalized)
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return payload


def clear_cache() -> None:
    _cache.clear()
