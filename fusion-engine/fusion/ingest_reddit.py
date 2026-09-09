"""Reddit adapter for the multi-platform social layer (no auth, public JSON).

Source: https://www.reddit.com/r/<sub>/new.json — public listing, no API key.
Needs only a descriptive User-Agent. Geolocation reuses the shared gazetteer
in fusion/ingest_social.py; posts with no resolvable place are kept but not
correlated spatially (same contract as Telegram).

    python -m fusion.ingest_reddit worldnews --since 2026-08-18 --until 2026-08-19
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from .ingest_social import SocialPost, _data_root, extract_keywords, geolocate

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (ParsonsOfInterest-MultiINT hackathon; contact: fusion-engine)"}

DEFAULT_SUBREDDITS = ["worldnews", "geopolitics", "CombatFootage", "UkraineWarVideoReport"]


def _clean(s: str | None) -> str:
    if not s:
        return ""
    s = html.unescape(s)
    s = re.sub(r"<[^>]+>", "", s)
    return s.strip()


def parse_listing(sub: str, payload: dict) -> tuple[list[SocialPost], str | None]:
    """Parse one /new.json payload -> (posts, after_cursor)."""
    posts: list[SocialPost] = []
    data = (payload or {}).get("data") or {}
    for child in data.get("children") or []:
        d = child.get("data") or {}
        pid = d.get("id")
        created = d.get("created_utc")
        if not pid or not created:
            continue
        title = _clean(d.get("title"))
        selftext = _clean(d.get("selftext"))
        text = (title + ("\n" + selftext if selftext else ""))[:4000]
        ts = datetime.fromtimestamp(float(created), tz=timezone.utc).isoformat()
        lat, lon, place = geolocate(text)
        channel = f"r/{sub}"
        permalink = d.get("permalink") or f"/r/{sub}/comments/{pid}/"
        url = permalink if permalink.startswith("http") else f"https://www.reddit.com{permalink}"
        score = d.get("score")
        comments = d.get("num_comments") or 0
        try:
            views = int(score or 0) + int(comments or 0)
        except (TypeError, ValueError):
            views = None
        post_hint = (d.get("post_hint") or "")
        media = bool(d.get("preview") or post_hint in ("image", "hosted:video", "rich:video")
                     or (d.get("url_overridden_by_dest") or "").lower().endswith((".jpg", ".png", ".gif", ".mp4")))
        posts.append(SocialPost(
            id=f"reddit:{channel}/{pid}", platform="reddit", ts=ts, channel=channel,
            text=text, url=url, lat=lat, lon=lon, place=place,
            keywords=extract_keywords(text), views=views, has_media=media,
        ))
    return posts, data.get("after")


def fetch_latest(sub: str, limit: int = 100) -> list[SocialPost]:
    r = requests.get(f"https://www.reddit.com/r/{sub}/new.json",
                     params={"limit": min(limit, 100)}, headers=HEADERS, timeout=30)
    if r.status_code == 429:
        log.warning("reddit r/%s rate-limited (429); backing off", sub)
        return []
    r.raise_for_status()
    posts, _ = parse_listing(sub, r.json())
    return sorted(posts, key=lambda p: p.ts)


def fetch_latest_all(subs: list[str] | None = None, delay_s: float = 1.0) -> list[SocialPost]:
    out: list[SocialPost] = []
    for sub in subs or DEFAULT_SUBREDDITS:
        try:
            out += fetch_latest(sub)
        except Exception as e:
            log.warning("reddit r/%s failed: %s", sub, e)
        time.sleep(delay_s)  # public JSON rate-limits bursts; space requests
    return sorted(out, key=lambda p: p.ts)


def fetch_channel(sub: str, since: str, until: str | None = None, max_pages: int = 50,
                  delay_s: float = 1.0) -> list[SocialPost]:
    """Walk r/<sub>/new backwards until posts older than `since` (ISO date)."""
    out: list[SocialPost] = []
    after: str | None = None
    for _ in range(max_pages):
        r = requests.get(f"https://www.reddit.com/r/{sub}/new.json",
                         params={"limit": 100, **({"after": after} if after else {})},
                         headers=HEADERS, timeout=30)
        if r.status_code == 429:
            log.warning("reddit r/%s rate-limited; stopping page walk", sub)
            break
        r.raise_for_status()
        posts, after = parse_listing(sub, r.json())
        if not posts:
            break
        for p in posts:
            if p.ts[:10] >= since and (until is None or p.ts[:10] < until):
                out.append(p)
        if min(p.ts for p in posts)[:10] < since or not after:
            break
        time.sleep(delay_s)
    out.sort(key=lambda p: p.ts)
    log.info("reddit r/%s: %d posts %s..%s (%d geolocated)", sub, len(out), since, until,
             sum(p.lat is not None for p in out))
    return out


# Alias so the generic registry can use a uniform name.
fetch_range = fetch_channel


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("subs", nargs="*", default=DEFAULT_SUBREDDITS)
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    allp: list[SocialPost] = []
    for s in a.subs:
        try:
            allp += fetch_channel(s, a.since, a.until)
        except Exception as e:
            log.warning("r/%s failed: %s", s, e)
    out = Path(a.out) if a.out else _data_root() / "replay" / f"{a.since}_reddit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([p.to_dict() for p in allp], ensure_ascii=False), encoding="utf-8")
    print(f"{len(allp)} posts -> {out}")
