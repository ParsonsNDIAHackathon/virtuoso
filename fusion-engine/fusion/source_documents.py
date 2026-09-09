"""Safe, bounded article-text retrieval for GDELT evidence selected for adjudication."""
from __future__ import annotations

import html
import os
import re
import threading
import time
from collections import OrderedDict
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests

from .preview import _meta, _public
from .bounded import BoundedCalls

UA = "Mozilla/5.0 (compatible; ParsonsOfInterest-MultiINT/0.1; source evidence retrieval)"
CACHE_TTL = 24 * 3600
FAILURE_TTL = 5 * 60
CACHE_MAX = 500
_cache: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()
_fetches = BoundedCalls()


def article_url_key(url: str) -> str | None:
    """Normalize article identity without dropping content-selecting query parameters."""
    try:
        parsed = urlparse(url.strip())
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        host = parsed.hostname.lower()
        port = parsed.port
        if port and (parsed.scheme.lower(), port) not in {("http", 80), ("https", 443)}:
            host = f"{host}:{port}"
        query = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True)
                 if not key.lower().startswith("utm_")
                 and key.lower() not in {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid"}]
        path = parsed.path.rstrip("/") or "/"
        # Shared publisher home pages or authentication redirects do not identify an article.
        if path.lower() in {"/", "/news", "/world", "/login", "/signin", "/consent", "/subscribe"}:
            if not any(key.lower() in {"id", "article", "article_id", "story", "story_id", "p"} for key, _ in query):
                return None
        return urlunparse((parsed.scheme.lower(), host, path, parsed.params, urlencode(sorted(query)), ""))
    except (ValueError, AttributeError):
        return None


class _ArticleParser(HTMLParser):
    blocks = {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "figcaption"}
    skipped = {"script", "style", "noscript", "svg", "nav", "header", "footer", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.block_depth = 0
        self.buffer: list[str] = []
        self.chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs):
        tag = tag.lower()
        if tag in self.skipped:
            self.skip_depth += 1
        elif not self.skip_depth and tag in self.blocks:
            if not self.block_depth:
                self.buffer = []
            self.block_depth += 1

    def handle_endtag(self, tag: str):
        tag = tag.lower()
        if tag in self.skipped and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in self.blocks and self.block_depth:
            self.block_depth -= 1
            if not self.block_depth:
                value = re.sub(r"\s+", " ", " ".join(self.buffer)).strip()
                if len(value) >= 20:
                    self.chunks.append(value)
                self.buffer = []

    def handle_data(self, data: str):
        if not self.skip_depth and self.block_depth and data.strip():
            self.buffer.append(data.strip())


def _safe_html(url: str) -> tuple[str, str, int]:
    """Fetch HTML while validating every redirect target and bounding bytes read."""
    current = url
    response = None
    for _ in range(5):
        parsed = urlparse(current)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not _public(parsed.hostname):
            raise ValueError("source host is not allowed")
        response = requests.get(
            current, headers={"User-Agent": UA, "Accept": "text/html,application/xhtml+xml"},
            timeout=float(os.getenv("FUSION_SOURCE_DOC_TIMEOUT_S", "12")), stream=True,
            allow_redirects=False,
        )
        if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("location"):
            target = urljoin(current, response.headers["location"])
            response.close()
            current = target
            continue
        break
    else:
        raise ValueError("source redirected too many times")
    if response is None:
        raise ValueError("source response unavailable")
    content_type = (response.headers.get("content-type") or "").lower()
    if response.status_code >= 400:
        status = response.status_code
        response.close()
        raise ValueError(f"source returned HTTP {status}")
    if "html" not in content_type and "xhtml" not in content_type:
        response.close()
        raise ValueError("source is not HTML")
    max_bytes = max(64_000, int(os.getenv("FUSION_SOURCE_DOC_MAX_BYTES", "2000000")))
    try:
        raw = bytearray()
        for chunk in response.iter_content(64 * 1024):
            raw.extend(chunk)
            if len(raw) >= max_bytes:
                del raw[max_bytes:]
                break
        return bytes(raw).decode(response.encoding or "utf-8", errors="replace"), current, response.status_code
    finally:
        response.close()


def document_text(url: str, max_chars: int | None = None, *, force: bool = False) -> dict:
    """Return extracted article paragraphs, cached by URL; failures are non-fatal evidence gaps."""
    max_chars = max_chars or max(1000, int(os.getenv("FUSION_SOURCE_DOC_MAX_CHARS", "24000")))
    now = time.time()
    with _lock:
        hit = _cache.get(url)
        if not force and hit and now - hit["fetched_at_epoch"] < (CACHE_TTL if hit["available"] else FAILURE_TTL):
            _cache.move_to_end(url)
            result = dict(hit)
            text = result.get("text", "")
            result["text"], result["truncated"] = text[:max_chars], hit.get("truncated", False) or len(text) > max_chars
            return result
    result = {
        "url": url, "resolved_url": url, "title": None, "text": "", "status": None,
        "available": False, "truncated": False, "fetched_at_epoch": now,
    }
    try:
        page, resolved_url, status = _fetches.call(
            lambda: _safe_html(url), timeout=float(os.getenv("FUSION_SOURCE_DOC_TIMEOUT_S", "12")),
            label="Article retrieval",
        )
        parser = _ArticleParser()
        parser.feed(page)
        # Publishers commonly repeat mobile/desktop paragraphs; retain one copy in source order.
        chunks = list(dict.fromkeys(parser.chunks))
        text = "\n\n".join(chunks)
        title = _meta(page, "og:title", "twitter:title")
        if not title:
            match = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
            title = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip() if match else None
        result.update(
            resolved_url=resolved_url, title=title[:300] if title else None, text=text[:100_000],
            status=status, available=bool(text), truncated=len(text) > 100_000,
        )
        if not text:
            result["error"] = "no article text found"
    except (requests.RequestException, ValueError, OSError) as error:
        result["error"] = str(error)[:160]
    with _lock:
        _cache[url] = result
        _cache.move_to_end(url)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    output = dict(result)
    text = output.get("text", "")
    output["text"], output["truncated"] = text[:max_chars], result["truncated"] or len(text) > max_chars
    return output
