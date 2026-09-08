"""Link preview: fetch a source page once and return its Open Graph / meta summary.

Used by the console's Inspector so a news article shows a card (title, description, image,
publisher) instead of an iframe that the publisher's frame policy would block anyway.
Guardrails: http(s) only, public hosts only, 10 s timeout, 1 MB cap, in-memory cache.
"""
from __future__ import annotations

import html
import ipaddress
import re
import socket
import threading
import time
from collections import OrderedDict
from urllib.parse import urlparse, urljoin

import requests

UA = "Mozilla/5.0 (compatible; ParsonsOfInterest-MultiINT/0.1; +hackathon link preview)"
_cache: "OrderedDict[str, dict]" = OrderedDict()
_lock = threading.Lock()
CACHE_MAX = 500
CACHE_TTL = 6 * 3600


def _public(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return False
    return True


def _meta(page: str, *names: str) -> str | None:
    for n in names:
        m = re.search(r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]*content=["\']([^"\']*)["\']' % re.escape(n), page, re.I) \
            or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]*(?:property|name)=["\']%s["\']' % re.escape(n), page, re.I)
        if m and m.group(1).strip():
            return html.unescape(m.group(1).strip())
    return None


def preview(url: str) -> dict:
    now = time.time()
    with _lock:
        hit = _cache.get(url)
        if hit and now - hit["fetched_at"] < CACHE_TTL:
            _cache.move_to_end(url)
            return hit
    p = urlparse(url)
    out = {"url": url, "host": p.hostname or "", "fetched_at": now, "title": None, "description": None,
           "image": None, "site_name": None, "error": None, "embeddable": False}
    if p.scheme not in ("http", "https") or not p.hostname:
        out["error"] = "unsupported url"
        return out
    if not _public(p.hostname):
        out["error"] = "host not allowed"
        return out
    try:
        r = requests.get(url, headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.5"}, timeout=10, stream=True)
        ctype = r.headers.get("content-type", "")
        raw = b""
        for chunk in r.iter_content(64 * 1024):
            raw += chunk
            if len(raw) > 1_000_000:
                break
        r.close()
        page = raw.decode(r.encoding or "utf-8", errors="replace") if "html" in ctype or raw[:200].lstrip().lower().startswith(b"<!doctype") or b"<html" in raw[:500].lower() else ""
        out["status"] = r.status_code
        xfo = (r.headers.get("x-frame-options") or "").lower()
        csp = (r.headers.get("content-security-policy") or "").lower()
        out["embeddable"] = not xfo and "frame-ancestors" not in csp
        if page:
            out["title"] = _meta(page, "og:title", "twitter:title") or (re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S) or [None, None])[1]
            if out["title"]:
                out["title"] = html.unescape(re.sub(r"\s+", " ", out["title"])).strip()[:300]
            out["description"] = (_meta(page, "og:description", "twitter:description", "description") or "")[:600] or None
            img = _meta(page, "og:image", "twitter:image")
            out["image"] = urljoin(url, img) if img else None
            out["site_name"] = _meta(page, "og:site_name")
            out["published"] = _meta(page, "article:published_time", "og:updated_time", "date")
        elif r.status_code >= 400:
            out["error"] = f"http {r.status_code}"
    except requests.RequestException as e:
        out["error"] = type(e).__name__
    with _lock:
        _cache[url] = out
        _cache.move_to_end(url)
        while len(_cache) > CACHE_MAX:
            _cache.popitem(last=False)
    return out
