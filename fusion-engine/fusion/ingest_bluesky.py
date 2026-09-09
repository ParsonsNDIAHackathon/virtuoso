"""Bluesky adapter for the multi-platform social layer (no auth, public API).

Source: https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts — keyless
public search (q, sort=latest, since/until, cursor). Geolocation reuses the
shared gazetteer in fusion/ingest_social.py.

    python -m fusion.ingest_bluesky "Strait of Hormuz" --since 2026-08-18 --until 2026-08-19
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .ingest_social import SocialPost, _data_root, extract_keywords, geolocate

log = logging.getLogger(__name__)
API = "https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts"
PDS = os.getenv("BLUESKY_PDS", "https://bsky.social")
_SESSION: dict = {}


def _login() -> str | None:
    """App-password session (com.atproto.server.createSession) when BLUESKY_HANDLE/APP_PASSWORD are set."""
    handle, pw = os.getenv("BLUESKY_HANDLE", "").strip(), os.getenv("BLUESKY_APP_PASSWORD", "").strip()
    if not handle or not pw:
        return None
    if _SESSION.get("jwt") and time.time() - _SESSION.get("at", 0) < 3600:
        return _SESSION["jwt"]
    r = requests.post(f"{PDS}/xrpc/com.atproto.server.createSession",
                      json={"identifier": handle, "password": pw}, headers=HEADERS, timeout=30)
    r.raise_for_status()
    _SESSION.update(jwt=r.json().get("accessJwt"), at=time.time())
    log.info("bluesky: authenticated session for %s", handle)
    return _SESSION["jwt"]


def _endpoint() -> tuple[str, dict]:
    """(url, headers): the account's PDS with a bearer token when logged in, else the public endpoint."""
    jwt = _login()
    if jwt:
        return f"{PDS}/xrpc/app.bsky.feed.searchPosts", {**HEADERS, "Authorization": f"Bearer {jwt}"}
    return API, HEADERS
HEADERS = {"User-Agent": "Mozilla/5.0 (ParsonsOfInterest-MultiINT hackathon)"}

DEFAULT_QUERIES = ["Strait of Hormuz", "tanker", "Hormuz", "IRGC"]


def _rkey(uri: str) -> str:
    return (uri or "").rstrip("/").split("/")[-1]


def parse_search(payload: dict) -> tuple[list[SocialPost], str | None]:
    posts: list[SocialPost] = []
    for item in (payload or {}).get("posts") or []:
        uri = item.get("uri") or ""
        author = (item.get("author") or {}).get("handle") or "unknown"
        record = item.get("record") or {}
        text = (record.get("text") or "")[:4000]
        created = record.get("createdAt") or item.get("indexedAt")
        if not uri or not created:
            continue
        try:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
        lat, lon, place = geolocate(text)
        rkey = _rkey(uri)
        url = f"https://bsky.app/profile/{author}/post/{rkey}"
        try:
            views = int(item.get("likeCount") or 0) + 2 * int(item.get("repostCount") or 0)
        except (TypeError, ValueError):
            views = None
        posts.append(SocialPost(
            id=f"bsky:{author}/{rkey}", platform="bluesky", ts=ts, channel=author,
            text=text, url=url, lat=lat, lon=lon, place=place,
            keywords=extract_keywords(text), views=views,
            has_media=bool(item.get("embed")),
        ))
    return posts, (payload or {}).get("cursor")


def _search(query: str, limit: int = 100, cursor: str | None = None,
            since: str | None = None, until: str | None = None) -> dict:
    params: dict = {"q": query, "limit": min(limit, 100), "sort": "latest"}
    if cursor:
        params["cursor"] = cursor
    if since:
        params["since"] = since
    if until:
        params["until"] = until
    url, headers = _endpoint()
    r = requests.get(url, params=params, headers=headers, timeout=30)
    if r.status_code == 401 and _SESSION.get('jwt'):
        _SESSION.clear()                     # expired token: log in again once
        url, headers = _endpoint()
        r = requests.get(url, params=params, headers=headers, timeout=30)
    r.raise_for_status()
    return r.json()


def fetch_latest(query: str, limit: int = 100) -> list[SocialPost]:
    posts, _ = parse_search(_search(query, limit=limit))
    return sorted(posts, key=lambda p: p.ts)


def fetch_latest_all(queries: list[str] | None = None, delay_s: float = 0.5) -> list[SocialPost]:
    out: list[SocialPost] = []
    seen: set[str] = set()
    for q in queries or DEFAULT_QUERIES:
        try:
            for p in fetch_latest(q):
                if p.id not in seen:
                    seen.add(p.id)
                    out.append(p)
        except Exception as e:
            log.warning("bluesky %r failed: %s", q, e)
        time.sleep(delay_s)
    return sorted(out, key=lambda p: p.ts)


def fetch_channel(query: str, since: str, until: str | None = None, max_pages: int = 50,
                  delay_s: float = 0.5) -> list[SocialPost]:
    """Page search results backwards with cursor until posts older than `since`."""
    out: list[SocialPost] = []
    seen: set[str] = set()
    cursor: str | None = None
    since_iso = f"{since}T00:00:00Z" if len(since) == 10 else since
    until_iso = f"{until}T00:00:00Z" if until and len(until) == 10 else until
    for _ in range(max_pages):
        payload = _search(query, cursor=cursor, since=since_iso, until=until_iso)
        posts, cursor = parse_search(payload)
        if not posts:
            break
        fresh = False
        for p in posts:
            if p.id in seen:
                continue
            seen.add(p.id)
            fresh = True
            if p.ts[:10] >= since and (until is None or p.ts[:10] < until):
                out.append(p)
        if not fresh or not cursor:
            break
        if min(p.ts for p in posts)[:10] < since:
            break
        time.sleep(delay_s)
    out.sort(key=lambda p: p.ts)
    log.info("bluesky %r: %d posts %s..%s (%d geolocated)", query, len(out), since, until,
             sum(p.lat is not None for p in out))
    return out


fetch_range = fetch_channel


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("queries", nargs="*", default=DEFAULT_QUERIES)
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    allp: list[SocialPost] = []
    for q in a.queries:
        try:
            allp += fetch_channel(q, a.since, a.until)
        except Exception as e:
            log.warning("%r failed: %s", q, e)
    out = Path(a.out) if a.out else _data_root() / "replay" / f"{a.since}_bluesky.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([p.to_dict() for p in allp], ensure_ascii=False), encoding="utf-8")
    print(f"{len(allp)} posts -> {out}")
