"""Platform-agnostic social OSINT layer.

Telegram was the first social source (see fusion/ingest_telegram.py). This module
adds a small registry so additional social websites can plug in with the same
contract and flow through the same correlator/graph as GDELT events:

  - SocialPost: one normalized post from any platform (platform, id, ts, text,
    url, lat/lon/place, keywords, views, has_media).
  - geolocate()/KEYWORDS/GAZETTEER shared by every adapter so correlations stay
    comparable across platforms.
  - social_to_event(): adapter from SocialPost -> OsintEvent (root_code SOCIAL).
  - is_social_event()/is_social_source(): platform-agnostic social detection
    (replaces hard-coded t.me/ checks).
  - enabled_platforms()/fetch_latest_all()/fetch_range_all(): fan-out across
    every configured adapter. Failures are isolated per platform/target.

Adding a new website means adding one adapter module exposing
fetch_latest()/fetch_range() (see ingest_reddit.py for the template) and
registering it in _ADAPTERS below. No pipeline/replay/frontend changes needed.

Keyless by design: every bundled adapter uses a no-auth public endpoint
(Telegram previews, Reddit public JSON, Bluesky public API, Mastodon public
timeline). Auth-gated APIs (X/Twitter, Facebook, Instagram) are intentionally
not bundled — see docs/DATA_SOURCES.md §2.

    python -m fusion.ingest_social --since 2026-08-18 --until 2026-08-19
    python -m fusion.ingest_social --platform reddit --since 2026-08-18 --live
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared geography + keywords (single copy; ingest_telegram re-exports these
# for backwards compatibility).
# ---------------------------------------------------------------------------

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
HOT_KEYWORDS = frozenset({"missile", "strike", "attack", "explosion", "drone", "uav",
                          "projectile", "seized", "boarded", "warship"})



def _apply_mission_config() -> None:
    """Mission override for the built-in Gulf gazetteer and keyword lists.

    The built-ins are the Hormuz instance. A JSON file named by FUSION_SOCIAL_CONFIG, or
    <FUSION_DATA_DIR>/config/social.json, may carry {"gazetteer": {name: [lat, lon]},
    "keywords": [...], "hot_keywords": [...]}; entries merge over the defaults so a new
    theater does not require editing this module."""
    global KEYWORDS, HOT_KEYWORDS
    path = os.getenv("FUSION_SOCIAL_CONFIG") or str(
        Path(os.getenv("FUSION_DATA_DIR") or Path(__file__).resolve().parent.parent / "data") / "config" / "social.json")
    try:
        cfg = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return
    except Exception as e:  # a malformed config must not take the ingest down
        log.warning("social config %s ignored: %s", path, e)
        return
    for name, coords in (cfg.get("gazetteer") or {}).items():
        if isinstance(coords, (list, tuple)) and len(coords) == 2:
            GAZETTEER[str(name).lower()] = (float(coords[0]), float(coords[1]))
    if cfg.get("keywords"):
        KEYWORDS = tuple(dict.fromkeys(list(KEYWORDS) + [str(k).lower() for k in cfg["keywords"]]))
    if cfg.get("hot_keywords"):
        HOT_KEYWORDS = frozenset(HOT_KEYWORDS | {str(k).lower() for k in cfg["hot_keywords"]})
    log.info("social config loaded from %s (%d gazetteer entries)", path, len(GAZETTEER))


_apply_mission_config()

_COORD_RE = re.compile(r"(-?\d{1,2}\.\d{3,})[,\s]+(-?\d{1,3}\.\d{3,})")


def geolocate(text: str) -> tuple[float | None, float | None, str | None]:
    """Resolve free text to (lat, lon, place) via explicit coords, else gazetteer."""
    m = _COORD_RE.search(text or "")
    if m:
        la, lo = float(m.group(1)), float(m.group(2))
        if -90 <= la <= 90 and -180 <= lo <= 180:
            return la, lo, f"{la:.3f},{lo:.3f}"
    low = (text or "").lower()
    best = None
    for name, ll in GAZETTEER.items():
        if name in low and (best is None or len(name) > len(best[0])):
            best = (name, ll)
    if best:
        return best[1][0], best[1][1], best[0].title()
    return None, None, None


def extract_keywords(text: str) -> list[str]:
    low = (text or "").lower()
    return [k for k in KEYWORDS if k in low]


# ---------------------------------------------------------------------------
# Normalized post
# ---------------------------------------------------------------------------

@dataclass
class SocialPost:
    id: str                  # "<prefix>:<account>/<post_id>", e.g. "tg:intelslava/123"
    platform: str = "telegram"   # telegram | reddit | bluesky | mastodon (+ future)
    ts: str = ""             # ISO-8601 UTC
    channel: str = ""        # account: channel name, r/<sub>, handle, acct
    text: str = ""
    url: str = ""
    lat: float | None = None
    lon: float | None = None
    place: str | None = None
    keywords: list[str] = field(default_factory=list)
    views: int | None = None     # platform engagement proxy (views/score/likes)
    has_media: bool = False

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SocialPost":
        d = dict(d)
        if "platform" not in d or not d["platform"]:
            d["platform"] = _infer_platform(d.get("id", ""), d.get("url", ""))
        return cls(**{k: d.get(k) for k in
                      ("id", "platform", "ts", "channel", "text", "url", "lat", "lon",
                       "place", "keywords", "views", "has_media")})


def _infer_platform(pid: str, url: str) -> str:
    if pid.startswith("tg:") or "t.me/" in (url or ""):
        return "telegram"
    if pid.startswith("reddit:") or "reddit.com" in (url or ""):
        return "reddit"
    if pid.startswith("bsky:") or "bsky.app" in (url or ""):
        return "bluesky"
    if pid.startswith(("mastodon:", "md:")) or "mastodon" in (url or ""):
        return "mastodon"
    return "telegram" if pid.startswith("tg:") else "unknown"


SOCIAL_PLATFORMS = ("telegram", "reddit", "bluesky", "mastodon")


def platform_of(value, url: str | None = None) -> str | None:
    """Platform name for a SocialPost, OsintEvent, store/API dict, or bare id; None if not social.
    Prefers the explicit `platform` field and falls back to the id prefix, never to domain strings."""
    if value is None:
        return None
    if isinstance(value, str):
        pid, purl, plat = value, url or "", None
    elif isinstance(value, dict):
        pid, purl, plat = str(value.get("id") or ""), value.get("url") or url or "", value.get("platform")
    else:
        pid, purl, plat = str(getattr(value, "id", "") or ""), getattr(value, "url", None) or url or "", getattr(value, "platform", None)
    if plat in SOCIAL_PLATFORMS:
        return plat
    inferred = _infer_platform(pid, purl)
    return inferred if inferred in SOCIAL_PLATFORMS else None


def social_to_event(p: SocialPost):
    """Adapter so posts from any platform flow through the same correlator/graph.

    Severity proxies: conflict keywords -> negative Goldstein; engagement
    (views/score/likes) -> mentions. source_domain encodes the platform so the
    graph/dashboard can attribute each event without per-platform branches.
    """
    from .ingest_gdelt import OsintEvent
    n_hot = len(HOT_KEYWORDS & set(p.keywords or []))
    platform = (p.platform or _infer_platform(p.id, p.url)).lower()
    if platform == "telegram":
        source_domain = f"t.me/{p.channel}" if p.channel else "t.me"
    elif platform == "reddit":
        ch = p.channel if p.channel.startswith("r/") else (f"r/{p.channel}" if p.channel else "r/unknown")
        source_domain = f"reddit.com/{ch}"
    elif platform == "bluesky":
        source_domain = "bsky.app"
    elif platform == "mastodon":
        source_domain = _mastodon_host(p.url) or "mastodon.social"
    else:
        source_domain = f"{platform or 'social'}.social"
    return OsintEvent(
        id=p.id, ts=p.ts, lat=p.lat, lon=p.lon, place=p.place or "", country="", geo_type=4,
        actor1=None, actor2=None, actor1_cc=None, actor2_cc=None,
        event_code="SOCIAL", root_code="SOCIAL", root_label="Social post",
        quad_class=4 if n_hot else 1, goldstein=-min(10.0, 3.0 * n_hot) if n_hot else 0.0,
        tone=-2.0 * n_hot, num_mentions=max(1, (p.views or 0) // 1000), num_sources=1,
        url=p.url, source_domain=source_domain, is_conflict=n_hot > 0,
        themes=[k.upper() for k in (p.keywords or [])], persons=[], orgs=[],
    )


def _mastodon_host(url: str) -> str | None:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1).lower() if m else None


# Social attribution helpers (replace hard-coded t.me/ checks).
SOCIAL_ROOT_CODE = "SOCIAL"
SOCIAL_DOMAINS = ("t.me/", "reddit.com", "bsky.app", "mastodon.social")


def is_social_source(source_domain: str | None, url: str | None = None) -> bool:
    sd = (source_domain or "").lower()
    if any(tok in sd for tok in ("t.me", "reddit.com", "bsky.app", "mastodon")):
        return True
    u = (url or "").lower()
    return any(tok in u for tok in ("t.me/", "reddit.com/", "bsky.app/", "mastodon"))


def is_social_event(e) -> bool:
    """Accept OsintEvent, SocialPost, or plain dicts from the store/API."""
    if isinstance(e, dict):
        if e.get("root_code") == SOCIAL_ROOT_CODE or e.get("event_code") == SOCIAL_ROOT_CODE:
            return True
        return is_social_source(e.get("source_domain"), e.get("url"))
    root = getattr(e, "root_code", None) or getattr(e, "event_code", None)
    if root == SOCIAL_ROOT_CODE:
        return True
    return is_social_source(getattr(e, "source_domain", None), getattr(e, "url", None))


# ---------------------------------------------------------------------------
# Platform registry + configuration
# ---------------------------------------------------------------------------

#: platform -> adapter module path (lazy import keeps telegram's shared-code
#: import cycle-free and keeps optional adapters cheap).
_ADAPTERS = {
    "telegram": "fusion.ingest_telegram",
    "reddit": "fusion.ingest_reddit",
    "bluesky": "fusion.ingest_bluesky",
    "mastodon": "fusion.ingest_mastodon",
}

PLATFORM_LABELS = {
    "telegram": "Telegram previews",
    "reddit": "Reddit public JSON",
    "bluesky": "Bluesky public API",
    "mastodon": "Mastodon public timeline",
}

ALL_PLATFORMS = tuple(_ADAPTERS)

# What each platform needs in .env before it will answer (all are account-level, never committed).
CREDENTIAL_HINT = {
    "bluesky": "set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD in .env (Settings > App passwords)",
    "mastodon": "set MASTODON_ACCESS_TOKEN in .env (Preferences > Development > New application, read scope)",
    "reddit": "set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env (script app; add REDDIT_USERNAME/REDDIT_PASSWORD)",
}


def failure_reason(platform: str, exc: Exception) -> str:
    """One line an operator can act on: the HTTP status in words plus the credential that fixes it."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    hint = CREDENTIAL_HINT.get(platform)
    if status in (401, 403):
        return f"access denied ({status}): login required" + (f"; {hint}" if hint else "")
    if status == 422:
        return "request rejected (422): the instance requires an authenticated user" + (f"; {hint}" if hint else "")
    if status == 429:
        return "rate limited (429): backing off"
    if status:
        return f"provider error ({status})"
    text = str(exc).strip() or exc.__class__.__name__
    return text[:120]


def _env_list(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [p.strip() for p in raw.split(",") if p.strip()]


def enabled_platforms(platforms: list[str] | str | None = None) -> list[str]:
    """Configured social platforms. Env SOCIAL_PLATFORMS overrides the default set."""
    if platforms is None:
        items = _env_list("SOCIAL_PLATFORMS", ",".join(ALL_PLATFORMS))
    elif isinstance(platforms, str):
        items = [p.strip().lower() for p in platforms.split(",") if p.strip()]
    else:
        items = [str(p).strip().lower() for p in platforms]
    known = [p for p in items if p in _ADAPTERS]
    unknown = [p for p in items if p not in _ADAPTERS]
    if unknown:
        log.warning("unknown social platforms ignored: %s", ",".join(unknown))
    return known or [p for p in ALL_PLATFORMS if p in items] or list(ALL_PLATFORMS)[:1]


def default_targets(platform: str) -> list[str]:
    """Default accounts/queries per platform (env-overridable)."""
    if platform == "telegram":
        from . import ingest_telegram as tg
        return list(tg.DEFAULT_CHANNELS)
    if platform == "reddit":
        return _env_list("SOCIAL_REDDIT_SUBS",
                         "worldnews,geopolitics,CombatFootage,UkraineWarVideoReport")
    if platform == "bluesky":
        return _env_list("SOCIAL_BLUESKY_QUERIES",
                         "Strait of Hormuz,tanker,Hormuz,IRGC")
    if platform == "mastodon":
        return _env_list("SOCIAL_MASTODON_INSTANCES", "mastodon.social")
    return []


def _adapter(platform: str):
    import importlib
    return importlib.import_module(_ADAPTERS[platform])


def _data_root():
    """data dir shared with the server: FUSION_DATA_DIR (from env or fusion-engine/.env), else ./data"""
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass
    return Path(os.getenv("FUSION_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))


def fetch_latest_all(platforms: list[str] | str | None = None,
                     targets: dict[str, list[str]] | None = None) -> list[SocialPost]:
    """Newest posts from every enabled platform (live polling). Per-target
    failures are isolated so one down website never blocks the others."""
    out: list[SocialPost] = []
    for plat in enabled_platforms(platforms):
        try:
            mod = _adapter(plat)
            wants = (targets or {}).get(plat)
            posts = mod.fetch_latest_all(wants) if hasattr(mod, "fetch_latest_all") else None
            if posts is None:  # adapter exposes only per-target fetch_latest
                posts = []
                for t in wants or default_targets(plat):
                    try:
                        posts += mod.fetch_latest(t)
                    except Exception as e:
                        log.warning("%s %s latest failed: %s", plat, t, e)
            out += posts
        except Exception as e:
            log.warning("social %s latest failed: %s", plat, e)
    out.sort(key=lambda p: p.ts or "")
    return out


def fetch_range_all(since: str, until: str | None = None,
                    platforms: list[str] | str | None = None,
                    targets: dict[str, list[str]] | None = None,
                    max_pages: int = 50) -> list[SocialPost]:
    """Page every enabled platform back to `since` (ISO date). Used for replay
    builds and timeline backfill."""
    out: list[SocialPost] = []
    for plat in enabled_platforms(platforms):
        try:
            mod = _adapter(plat)
        except Exception as e:
            log.warning("social %s unavailable: %s", plat, e)
            continue
        for t in (targets or {}).get(plat) or default_targets(plat):
            try:
                if hasattr(mod, "fetch_channel"):
                    out += mod.fetch_channel(t, since, until, max_pages=max_pages)
                elif hasattr(mod, "fetch_range"):
                    out += mod.fetch_range(t, since, until, max_pages=max_pages)
            except Exception as e:
                log.warning("social %s %s range failed: %s", plat, t, e)
    out.sort(key=lambda p: p.ts or "")
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Multi-platform social fetch (keyless adapters)")
    ap.add_argument("--platform", default=None, help="comma-separated subset (default: SOCIAL_PLATFORMS)")
    ap.add_argument("--since", default=None, help="ISO date start for range fetch")
    ap.add_argument("--until", default=None)
    ap.add_argument("--live", action="store_true", help="fetch latest instead of range")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    plats = enabled_platforms(a.platform) if a.platform else None
    if a.live or not a.since:
        posts = fetch_latest_all(plats)
    else:
        posts = fetch_range_all(a.since, a.until, plats)
    tag = (a.since or "latest")
    if a.out:
        out = Path(a.out)
    elif a.platform and "," not in a.platform and len(enabled_platforms(a.platform)) == 1:
        out = _data_root() / "replay" / f"{tag}_{enabled_platforms(a.platform)[0]}.json"
    else:
        out = _data_root() / "replay" / f"{tag}_social.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([p.to_dict() for p in posts], ensure_ascii=False), encoding="utf-8")
    print(f"{len(posts)} posts ({', '.join(enabled_platforms(plats) if plats else enabled_platforms())}) -> {out}")
    for p in [p for p in posts if p.lat is not None][:10]:
        print(f"  {p.ts[11:16] if len(p.ts) > 16 else p.ts} {p.platform:9s} {p.channel:20s} {(p.place or ''):18s} {p.text[:90]!r}")
