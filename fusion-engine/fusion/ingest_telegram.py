"""Telegram public-channel ingest via the no-auth web preview (https://t.me/s/<channel>).

Works for public channels that allow previews; pages backwards with ?before=<msg_id>. No account,
no API key, no scraping of private content. One adapter of the multi-platform
social layer — see fusion/ingest_social.py for the registry (Reddit, Bluesky,
Mastodon share the same SocialPost contract). Geolocation is by place-name
matching against a small gazetteer plus any lat/lon pattern in the text; posts
with no resolvable place are kept but not correlated spatially.

    python -m fusion.ingest_telegram intelslava --since 2026-08-18 --until 2026-08-19
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

from .ingest_social import (
    GAZETTEER,
    KEYWORDS,
    SocialPost,
    extract_keywords,
    geolocate,
    social_to_event,
)

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (ParsonsOfInterest-MultiINT hackathon)"}

# Public channels that are active and preview-enabled (checked 2026-09-08). Extend freely.
DEFAULT_CHANNELS = ["intelslava", "Middle_East_Spectator", "iswnews", "eskannews_com"]   # preview-enabled and active as of 2026-09-08

# GAZETTEER / KEYWORDS / SocialPost / geolocate / social_to_event live in
# fusion/ingest_social.py (single shared copy, incl. Persian/Arabic spellings);
# re-exported here so existing imports keep working.
__all__ = ["DEFAULT_CHANNELS", "GAZETTEER", "KEYWORDS", "SocialPost", "geolocate",
           "social_to_event", "parse_page", "fetch_channel", "fetch_latest",
           "fetch_latest_all", "fetch_range"]

_POST_RE = re.compile(r'<div class="tgme_widget_message_wrap(.*?)(?=<div class="tgme_widget_message_wrap|$)', re.S)
_ID_RE = re.compile(r'data-post="([^"/]+)/(\d+)"')
_TIME_RE = re.compile(r'<time datetime="([^"]+)"')
_TEXT_RE = re.compile(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', re.S)
_VIEWS_RE = re.compile(r'<span class="tgme_widget_message_views">([^<]+)</span>')
_COORD_RE = re.compile(r"(-?\d{1,2}\.\d{3,})[,\s]+(-?\d{1,3}\.\d{3,})")


def _views(s: str | None) -> int | None:
    if not s:
        return None
    s = s.strip().upper().replace(",", "")
    mult = {"K": 1_000, "M": 1_000_000}.get(s[-1], 1)
    try:
        return int(float(s.rstrip("KM")) * mult)
    except ValueError:
        return None


def parse_page(channel: str, page_html: str) -> list[SocialPost]:
    posts = []
    for block in _POST_RE.finditer(page_html):
        b = block.group(1)
        idm, tm = _ID_RE.search(b), _TIME_RE.search(b)
        if not idm or not tm:
            continue
        msg_id = idm.group(2)
        txm = _TEXT_RE.search(b)
        text = html.unescape(re.sub(r"<br\s*/?>", "\n", txm.group(1))) if txm else ""
        text = re.sub(r"<[^>]+>", "", text).strip()
        ts = datetime.fromisoformat(tm.group(1)).astimezone(timezone.utc).isoformat()
        lat, lon, place = geolocate(text)
        vm = _VIEWS_RE.search(b)
        posts.append(SocialPost(
            id=f"tg:{channel}/{msg_id}", platform="telegram", ts=ts, channel=channel, text=text,
            url=f"https://t.me/{channel}/{msg_id}", lat=lat, lon=lon, place=place,
            keywords=extract_keywords(text),
            views=_views(vm.group(1) if vm else None),
            has_media='tgme_widget_message_photo' in b or 'tgme_widget_message_video' in b,
        ))
    return posts


def fetch_channel(channel: str, since: str, until: str | None = None, max_pages: int = 400,
                  delay_s: float = 0.6) -> list[SocialPost]:
    """Walk a public channel backwards until posts older than `since` (ISO date) appear."""
    url = f"https://t.me/s/{channel}"
    out: list[SocialPost] = []
    before = None
    for page in range(max_pages):
        r = requests.get(url, params={"before": before} if before else None, headers=HEADERS, timeout=30)
        if r.status_code != 200:
            log.warning("%s: HTTP %s", channel, r.status_code)
            break
        posts = parse_page(channel, r.text)
        if not posts:
            break
        ids = [int(p.id.rsplit("/", 1)[1]) for p in posts]
        new_before = min(ids)
        if before is not None and new_before >= before:
            break
        before = new_before
        for p in posts:
            if p.ts[:10] >= since and (until is None or p.ts[:10] < until):
                out.append(p)
        oldest = min(p.ts for p in posts)
        if oldest[:10] < since:
            break
        time.sleep(delay_s)
    out.sort(key=lambda p: p.ts)
    log.info("%s: %d posts %s..%s (%d geolocated)", channel, len(out), since, until,
             sum(p.lat is not None for p in out))
    return out


# Alias so the generic registry can use a uniform name.
fetch_range = fetch_channel


def fetch_latest(channel: str) -> list[SocialPost]:
    """Newest ~20 posts (live polling)."""
    r = requests.get(f"https://t.me/s/{channel}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return sorted(parse_page(channel, r.text), key=lambda p: p.ts)

def _data_root():
    """data dir shared with the server: FUSION_DATA_DIR (from env or fusion-engine/.env), else ./data"""
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass
    return Path(os.getenv("FUSION_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))


def fetch_latest_all(channels: list[str] | None = None, delay_s: float = 0.3) -> list[SocialPost]:
    """Newest posts from every channel (multi-platform registry entry point)."""
    out: list[SocialPost] = []
    for ch in channels or DEFAULT_CHANNELS:
        try:
            out += fetch_latest(ch)
        except Exception as e:
            log.warning("telegram %s failed: %s", ch, e)
        time.sleep(delay_s)
    return sorted(out, key=lambda p: p.ts)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("channels", nargs="*", default=DEFAULT_CHANNELS)
    ap.add_argument("--since", required=True)
    ap.add_argument("--until", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    allp = []
    for ch in a.channels:
        try:
            allp += fetch_channel(ch, a.since, a.until)
        except Exception as e:
            log.warning("%s failed: %s", ch, e)
    root = _data_root().parent  # data dir parent; see _data_root()
    out = Path(a.out) if a.out else _data_root() / "replay" / f"{a.since}_telegram.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([p.to_dict() for p in allp], ensure_ascii=False), encoding="utf-8")
    print(f"{len(allp)} posts -> {out}")
    for p in [p for p in allp if p.lat is not None][:10]:
        print(f"  {p.ts[11:16]} {p.channel:14s} {p.place:18s} {p.text[:90]!r}")
