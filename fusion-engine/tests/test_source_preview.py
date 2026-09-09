"""Server-side source preview: extraction, SSRF guards, caching, endpoint wiring.
No network calls (fixtures + monkeypatched sockets/requests only).

Run:  env/bin/python -m tests.test_source_preview
"""
from __future__ import annotations

import socket

import fusion.source_preview as sp
from fusion.source_preview import PreviewError, extract_preview, preview_url, validate_url

PUBLIC = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
PRIVATE = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))]
LOOPBACK = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
LINK_LOCAL = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", 0))]


def _patch(obj, name, value):
    old = getattr(obj, name)
    setattr(obj, name, value)
    return old


ARTICLE_HTML = """<html><head><title>Fallback title</title>
<meta property="og:title" content="Tanker hit near Hormuz" />
<meta name="description" content="Short meta summary." />
<meta property="og:description" content="Crew killed in vessel strike, officials say." />
<meta property="og:image" content="/img/tanker.jpg" />
<script>var evil = 1;</script><style>.x{color:red}</style></head>
<body><nav>Home | World</nav>
<h1>Tanker hit near Hormuz</h1>
<p>Hi</p>
<p>A vessel was hit by a projectile while exiting the Strait of Hormuz on Monday, maritime officials said.</p>
<p>The crew reported an explosion on the port side and requested assistance from nearby warships in the area.</p>
</body></html>"""


def test_extract_prefers_og_tags():
    out = extract_preview(ARTICLE_HTML, "https://example.com/news/a")
    assert out["title"] == "Tanker hit near Hormuz", out["title"]
    assert out["description"] == "Crew killed in vessel strike, officials say.", out["description"]
    assert out["image"] == "https://example.com/img/tanker.jpg", out["image"]
    assert "projectile" in out["text"] and "explosion" in out["text"]
    assert "evil" not in out["text"] and "Hi" not in out["text"].split("\n\n")[0]


def test_extract_falls_back_to_title_and_longest_blocks():
    html = "<html><head><title>Plain page &amp; more</title></head><body><p>x</p></body></html>"
    out = extract_preview(html, "https://example.com/")
    assert out["title"] == "Plain page & more", out["title"]
    assert out["description"] is None and out["image"] is None


def test_validate_rejects_bad_schemes_and_credentials():
    for bad in ("ftp://example.com/a", "file:///etc/passwd", "javascript:alert(1)",
                "https://user:pass@example.com/", "https://example.com:8080/a",
                "", "https://"):
        try:
            validate_url(bad)
        except PreviewError:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_validate_rejects_non_public_hosts():
    old = _patch(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError("dns fail")))
    try:
        try:
            validate_url("https://nonexistent.invalid/a")
            raise AssertionError("accepted unresolvable host")
        except PreviewError:
            pass
    finally:
        _patch(socket, "getaddrinfo", old)
    for infos in (PRIVATE, LOOPBACK, LINK_LOCAL):
        def _fake(*a, _infos=infos, **k):
            return _infos
        old = _patch(socket, "getaddrinfo", _fake)
        try:
            try:
                validate_url("https://example.com/a")
                raise AssertionError(f"accepted {infos[0][4][0]}")
            except PreviewError:
                pass
        finally:
            _patch(socket, "getaddrinfo", old)


def test_validate_accepts_public_host():
    old = _patch(socket, "getaddrinfo", lambda *a, **k: PUBLIC)
    try:
        assert validate_url("https://example.com/a") == "https://example.com/a"
    finally:
        _patch(socket, "getaddrinfo", old)


class _FakeResp:
    def __init__(self, status=200, headers=None, body=b"", url="https://example.com/a"):
        self.status_code = status
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}
        self.body = body
        self.url = url
        self.encoding = "utf-8"

    def iter_content(self, _n):
        yield self.body


def test_preview_url_happy_path_and_cache():
    import requests
    sp.clear_cache()
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        assert kwargs.get("allow_redirects") is False
        return _FakeResp(body=ARTICLE_HTML.encode())
    old_dns = _patch(socket, "getaddrinfo", lambda *a, **k: PUBLIC)
    old_get = _patch(requests, "get", fake_get)
    try:
        first = preview_url("https://example.com/news/a")
        second = preview_url("https://example.com/news/a")
    finally:
        _patch(socket, "getaddrinfo", old_dns)
        _patch(requests, "get", old_get)
    assert first["title"] == "Tanker hit near Hormuz" and first["error"] is None
    assert first["site"] == "example.com" and first["fetched_at"]
    assert calls == ["https://example.com/news/a"], calls  # second served from cache
    assert second == first
    sp.clear_cache()


def _fake_dns(host, *a, **k):
    """Behave like real DNS: known public host resolves publicly, anything
    else (including literal IPs) resolves to itself."""
    if host == "example.com":
        return PUBLIC
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 0))]


def test_preview_url_revalidates_redirects():
    import requests
    sp.clear_cache()
    seen: list[str] = []

    def fake_get(url, **kwargs):
        seen.append(url)
        if url == "https://example.com/start":
            return _FakeResp(status=302, headers={"location": "http://10.0.0.5/evil"}, url=url)
        raise AssertionError("should not fetch private hop")
    old_dns = _patch(socket, "getaddrinfo", _fake_dns)
    old_get = _patch(requests, "get", fake_get)
    try:
        out = preview_url("https://example.com/start")
    finally:
        _patch(socket, "getaddrinfo", old_dns)
        _patch(requests, "get", old_get)
    assert out["error"] and "non-public" in out["error"], out
    assert seen == ["https://example.com/start"], seen
    sp.clear_cache()


def test_preview_url_http_error_is_payload_not_raise():
    import requests
    sp.clear_cache()
    old_dns = _patch(socket, "getaddrinfo", lambda *a, **k: PUBLIC)
    old_get = _patch(requests, "get", lambda url, **k: _FakeResp(status=404, body=b"nope"))
    try:
        out = preview_url("https://example.com/missing")
    finally:
        _patch(socket, "getaddrinfo", old_dns)
        _patch(requests, "get", old_get)
    assert out["error"] and "404" in out["error"] and out["text"] == ""
    sp.clear_cache()


def test_preview_url_image_content():
    import requests
    sp.clear_cache()
    headers = {"content-type": "image/jpeg"}
    old_dns = _patch(socket, "getaddrinfo", lambda *a, **k: PUBLIC)
    old_get = _patch(requests, "get", lambda url, **k: _FakeResp(headers=headers, url="https://example.com/pic.jpg"))
    try:
        out = preview_url("https://example.com/pic.jpg")
    finally:
        _patch(socket, "getaddrinfo", old_dns)
        _patch(requests, "get", old_get)
    assert out["image"] == "https://example.com/pic.jpg" and out["error"] is None
    sp.clear_cache()


def test_endpoint_wiring():
    import os
    os.environ["FUSION_STORE"] = "memory"
    from fastapi import HTTPException
    import app.server as srv
    assert "/api/source/preview" in {r.path for r in srv.app.routes if hasattr(r, "path")}
    try:
        srv.source_preview("ftp://example.com/a")
        raise AssertionError("expected 422")
    except HTTPException as e:
        assert e.status_code == 422


if __name__ == "__main__":
    for name, fn in sorted({k: v for k, v in globals().items() if k.startswith("test_")}.items()):
        fn()
        print("ok", name)
    print("all source-preview tests passed")
