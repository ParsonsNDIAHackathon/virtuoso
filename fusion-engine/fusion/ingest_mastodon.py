"""Mastodon adapter for the multi-platform social layer (no auth, public timeline).

Source: https://<instance>/api/v1/timelines/public — keyless public timeline
(limit, max_id paging). Geolocation reuses the shared gazetteer in
fusion/ingest_social.py. The public firehose is unfiltered, so only posts that
mention a gazetteer place or coordinates are kept for range fetches.

    python -m fusion.ingest_mastodon mastodon.social --since 2026-08-18 --until 2026-08-19
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .ingest_social import SocialPost, _data_root, extract_keywords, geolocate

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (ParsonsOfInterest-MultiINT hackathon)"}


def _headers() -> dict:
    """MASTODON_ACCESS_TOKEN (an application token from the configured instance) unlocks the timelines
    that mastodon.social now refuses to anonymous clients (HTTP 422 'requires an authenticated user')."""
    tok = os.getenv("MASTODON_ACCESS_TOKEN", "").strip()
    return {**HEADERS, "Authorization": f"Bearer {tok}"} if tok else dict(HEADERS)

DEFAULT_INSTANCES = ["mastodon.social"]


def _strip_content(content_html: str | None) -> str:
    if not content_html:
        return ""
    text = re.sub(r"</p\s*>", "\n", content_html, flags=re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()[:4000]


def parse_statuses(instance: str, items: list[dict]) -> list[SocialPost]:
    posts: list[SocialPost] = []
    host = (instance or "mastodon.social").lower()
    for s in items or []:
        sid = str(s.get("id") or "")
        created = s.get("created_at")
        if not sid or not created:
            continue
        try:
            ts = datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(timezone.utc).isoformat()
        except ValueError:
            continue
        text = _strip_content(s.get("content"))
        spoiler = (s.get("spoiler_text") or "").strip()
        if spoiler:
            text = f"{spoiler}\n{text}" if text else spoiler
        lat, lon, place = geolocate(text)
        acct = ((s.get("account") or {}).get("acct") or "unknown").lstrip("@")
        url = s.get("url") or f"https://{host}/@{acct}/{sid}"
        try:
            views = int(s.get("favourites_count") or 0) + int(s.get("reblogs_count") or 0)
        except (TypeError, ValueError):
            views = None
        media = s.get("media_attachments")
        posts.append(SocialPost(
            id=f"mastodon:{acct}/{sid}", platform="mastodon", ts=ts, channel=acct,
            text=text, url=url, lat=lat, lon=lon, place=place,
            keywords=extract_keywords(text), views=views,
            has_media=bool(media),
        ))
    return posts


def _page(instance: str, max_id: str | None = None, limit: int = 40) -> list[dict]:
    params: dict = {"limit": min(limit, 40), "local": "false"}
    if max_id:
        params["max_id"] = max_id
    r = requests.get(f"https://{instance}/api/v1/timelines/public",
                     params=params, headers=_headers(), timeout=30)
    r.raise_for_status()
    payload = r.json()
    return payload if isinstance(payload, list) else []


DEFAULT_QUERIES = ["Strait of Hormuz", "tanker", "Hormuz", "IRGC"]


def _search(instance: str, query: str, limit: int = 40) -> list[dict]:
    """Statuses matching a term (api/v2/search, needs the read scope). The public timeline is untargeted and
    yields almost nothing geolocatable, so with a token the collector also searches the configured terms."""
    r = requests.get(f"https://{instance}/api/v2/search", params={"q": query, "type": "statuses", "limit": min(limit, 40)},
                     headers=_headers(), timeout=30)
    r.raise_for_status()
    payload = r.json()
    return payload.get("statuses", []) if isinstance(payload, dict) else []


def fetch_latest(instance: str, limit: int = 40) -> list[SocialPost]:
    items = list(_page(instance, limit=limit))
    if os.getenv("MASTODON_ACCESS_TOKEN", "").strip():
        seen = {str(s.get("id")) for s in items}
        queries = [q.strip() for q in os.getenv("MASTODON_QUERIES", ",".join(DEFAULT_QUERIES)).split(",") if q.strip()]
        for q in queries:
            try:
                for s in _search(instance, q, limit):
                    if str(s.get("id")) not in seen:
                        seen.add(str(s.get("id")))
                        items.append(s)
            except Exception as e:                       # search scope missing: timeline still counts
                log.warning("mastodon %s search %r failed: %s", instance, q, e)
                break
    return sorted(parse_statuses(instance, items), key=lambda p: p.ts)


def fetch_latest_all(instances: list[str] | None = None, delay_s: float = 0.5) -> list[SocialPost]:
    out: list[SocialPost] = []
    for inst in instances or DEFAULT_INSTANCES:
        try:
            out += fetch_latest(inst)
        except Exception as e:
            log.warning("mastodon %s failed: %s", inst, e)
        time.sleep(delay_s)
    return sorted(out, key=lambda p: p.ts)


def fetch_channel(instance: str, since: str, until: str | None = None, max_pages: int = 50,
                  delay_s: float = 0.5) -> list[SocialPost]:
    """Walk the public timeline backwards until statuses older than `since`."""
    out: list[SocialPost] = []
    max_id: str | None = None
    for _ in range(max_pages):
        items = _page(instance, max_id)
        if not items:
            break
        posts = parse_statuses(instance, items)
        if not posts:
            max_id = str(min(int(s["id"]) for s in items if str(s.get("id", "")).isdigit())) \
                if any(str(s.get("id", "")).isdigit() for s in items) else None
            if not max_id:
                break
            continue
        for p in posts:
            # Firehose is mostly irrelevant chatter: keep geolocated posts in range.
            if p.lat is None:
                continue
            if p.ts[:10] >= since and (until is None or p.ts[:10] < until):
                out.append(p)
        oldest = min(p.ts for p in posts)
        ids = [s["id"] for s in items if s.get("id")]
        try:
            max_id = str(min(int(i) for i in ids if str(i).isdigit()) - 1)
        except ValueError:
            break
        if oldest[:10] < since:
            break
        time.sleep(delay_s)
    out.sort(key=lambda p: p.ts)
    log.info("mastodon %s: %d geolocated posts %s..%s", instance, len(out), since, until)
    return out


fetch_range = fetch_channel


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("instances", nargs="*", default=DEFAULT_INSTANCES)
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    allp: list[SocialPost] = []
    for inst in a.instances:
        try:
            allp += fetch_channel(inst, a.since, a.until)
        except Exception as e:
            log.warning("%s failed: %s", inst, e)
    out = Path(a.out) if a.out else _data_root() / "replay" / f"{a.since}_mastodon.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([p.to_dict() for p in allp], ensure_ascii=False), encoding="utf-8")
    print(f"{len(allp)} posts -> {out}")
