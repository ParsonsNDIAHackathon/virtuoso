"""Telegram public-channel ingest via the no-auth web preview (https://t.me/s/<channel>).

Works for public channels that allow previews; pages backwards with ?before=<msg_id>. No account,
no API key, no scraping of private content. Used as the "social" OSINT stream for both live polling
and historical replay. Geolocation is by place-name matching against a small gazetteer plus any
lat/lon pattern in the text; posts with no resolvable place are kept but not correlated spatially.

    python -m fusion.ingest_telegram intelslava --since 2026-08-18 --until 2026-08-19
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "Mozilla/5.0 (ParsonsOfInterest-MultiINT hackathon)"}

# Public channels that are active and preview-enabled (checked 2026-09-08). Extend freely.
DEFAULT_CHANNELS = ["intelslava", "Middle_East_Spectator", "iswnews", "eskannews_com"]   # preview-enabled and active as of 2026-09-08

# Minimal gazetteer for the Gulf scenario; (lat, lon). Extend per scenario.
GAZETTEER = {
    "hormuz": (26.57, 56.25), "strait of hormuz": (26.57, 56.25), "fujairah": (25.12, 56.34),
    "bandar abbas": (27.19, 56.28), "dubai": (25.20, 55.27), "abu dhabi": (24.45, 54.38),
    "musandam": (26.20, 56.25), "gulf of oman": (24.50, 58.00), "persian gulf": (26.50, 52.50),
    "qeshm": (26.80, 55.90), "jebel ali": (25.01, 55.06), "khor fakkan": (25.34, 56.35),
    "sohar": (24.36, 56.75), "muscat": (23.59, 58.41), "ras tanura": (26.64, 50.16),
    "kharg": (29.23, 50.32), "tehran": (35.69, 51.39), "bushehr": (28.97, 50.84),
    "lavan": (26.80, 53.28), "sirri": (25.90, 54.53), "abu musa": (25.87, 55.03),
    "tel aviv": (32.08, 34.78), "jerusalem": (31.77, 35.21), "kyiv": (50.45, 30.52),
    "moscow": (55.75, 37.62), "washington": (38.90, -77.04), "riyadh": (24.71, 46.68),
    "doha": (25.29, 51.53), "kuwait": (29.37, 47.98), "baghdad": (33.31, 44.37),
    # Persian / Arabic spellings (ISWNews, eskannews and similar channels post in Farsi/Arabic)
    "تنگه هرمز": (26.57, 56.25), "هرمز": (26.57, 56.25), "مضيق هرمز": (26.57, 56.25),
    "لارک": (26.85, 56.36), "لارك": (26.85, 56.36), "قشم": (26.80, 55.90),
    "بندرعباس": (27.19, 56.28), "بندر عباس": (27.19, 56.28), "جاسک": (25.64, 57.77),
    "خلیج فارس": (26.50, 52.50), "الخليج": (26.50, 52.50), "دریای عمان": (24.50, 58.00), "خليج عمان": (24.50, 58.00),
    "فجیره": (25.12, 56.34), "الفجيرة": (25.12, 56.34), "دبی": (25.20, 55.27), "دبي": (25.20, 55.27),
    "ابوظبی": (24.45, 54.38), "أبوظبي": (24.45, 54.38), "امارات": (24.45, 54.38), "الإمارات": (24.45, 54.38),
    "مسقط": (23.59, 58.41), "عمان": (23.59, 58.41), "تهران": (35.69, 51.39), "طهران": (35.69, 51.39),
    "بوشهر": (28.97, 50.84), "خارک": (29.23, 50.32), "کیش": (26.53, 53.98), "ابوموسی": (25.87, 55.03),
    "تل آویو": (32.08, 34.78), "تل أبيب": (32.08, 34.78), "ریاض": (24.71, 46.68), "الرياض": (24.71, 46.68),
}
KEYWORDS = ("tanker", "vessel", "ship", "missile", "drone", "uav", "strike", "attack", "explosion",
            "irgc", "navy", "warship", "escort", "seized", "boarded", "projectile", "airspace", "notam",
            # Persian / Arabic
            "نفتکش", "ناقلة", "کشتی", "سفينة", "موشک", "صاروخ", "پهپاد", "مسيرة", "حمله", "هجوم",
            "انفجار", "سپاه", "الحرس", "توقیف", "احتجاز", "ناوگان", "ناو")


@dataclass
class SocialPost:
    id: str                  # "tg:<channel>/<msg_id>"
    ts: str                  # ISO-8601 UTC
    channel: str
    text: str
    url: str
    lat: float | None
    lon: float | None
    place: str | None
    keywords: list[str] = field(default_factory=list)
    views: int | None = None
    has_media: bool = False

    def to_dict(self):
        return asdict(self)


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


def geolocate(text: str) -> tuple[float | None, float | None, str | None]:
    m = _COORD_RE.search(text)
    if m:
        la, lo = float(m.group(1)), float(m.group(2))
        if -90 <= la <= 90 and -180 <= lo <= 180:
            return la, lo, f"{la:.3f},{lo:.3f}"
    low = text.lower()
    # longest matching place name wins
    best = None
    for name, ll in GAZETTEER.items():
        if name in low and (best is None or len(name) > len(best[0])):
            best = (name, ll)
    if best:
        return best[1][0], best[1][1], best[0].title()
    return None, None, None


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
            id=f"tg:{channel}/{msg_id}", ts=ts, channel=channel, text=text,
            url=f"https://t.me/{channel}/{msg_id}", lat=lat, lon=lon, place=place,
            keywords=[k for k in KEYWORDS if k in text.lower()],
            views=_views(vm.group(1) if vm else None),
            has_media='tgme_widget_message_photo' in b or 'tgme_widget_message_video' in b,
        ))
    return posts


def social_to_event(p: SocialPost):
    """Adapter so social posts flow through the same correlator/graph as GDELT events.
    Severity proxies: conflict keywords -> negative Goldstein; views -> mentions."""
    from .ingest_gdelt import OsintEvent
    hot = {"missile", "strike", "attack", "explosion", "drone", "uav", "projectile", "seized", "boarded", "warship"}
    n_hot = len(hot & set(p.keywords))
    return OsintEvent(
        id=p.id, ts=p.ts, lat=p.lat, lon=p.lon, place=p.place or "", country="", geo_type=4,
        actor1=None, actor2=None, actor1_cc=None, actor2_cc=None,
        event_code="SOCIAL", root_code="SOCIAL", root_label="Social post",
        quad_class=4 if n_hot else 1, goldstein=-min(10.0, 3.0 * n_hot) if n_hot else 0.0,
        tone=-2.0 * n_hot, num_mentions=max(1, (p.views or 0) // 1000), num_sources=1,
        url=p.url, source_domain=f"t.me/{p.channel}", is_conflict=n_hot > 0,
        themes=[k.upper() for k in p.keywords], persons=[], orgs=[],
    )


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


def fetch_latest(channel: str) -> list[SocialPost]:
    """Newest ~20 posts (live polling)."""
    r = requests.get(f"https://t.me/s/{channel}", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return sorted(parse_page(channel, r.text), key=lambda p: p.ts)


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
    root = Path(__file__).resolve().parent.parent
    out = Path(a.out) if a.out else root / "data" / "replay" / f"{a.since}_telegram.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([p.to_dict() for p in allp], ensure_ascii=False), encoding="utf-8")
    print(f"{len(allp)} posts -> {out}")
    for p in [p for p in allp if p.lat is not None][:10]:
        print(f"  {p.ts[11:16]} {p.channel:14s} {p.place:18s} {p.text[:90]!r}")
