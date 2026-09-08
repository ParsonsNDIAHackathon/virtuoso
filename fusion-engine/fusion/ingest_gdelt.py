"""GDELT 2.0 ingest: OSINT event stream (15-minute cadence) + Global Knowledge Graph.

Sources
  https://data.gdeltproject.org/gdeltv2/lastupdate.txt   -> latest export/mentions/gkg zips
  Codebook: http://data.gdeltproject.org/documentation/GDELT-Event_Codebook-V2.0.pdf
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)

BASE = "https://data.gdeltproject.org/gdeltv2/"
LASTUPDATE = BASE + "lastupdate.txt"

# GDELT 2.0 export.CSV column positions (61 columns, tab separated, no header)
COL = {
    "GLOBALEVENTID": 0, "SQLDATE": 1, "Actor1Code": 5, "Actor1Name": 6, "Actor1CountryCode": 7,
    "Actor1Type1Code": 12, "Actor2Code": 15, "Actor2Name": 16, "Actor2CountryCode": 17,
    "Actor2Type1Code": 22, "IsRootEvent": 25, "EventCode": 26, "EventBaseCode": 27,
    "EventRootCode": 28, "QuadClass": 29, "GoldsteinScale": 30, "NumMentions": 31,
    "NumSources": 32, "NumArticles": 33, "AvgTone": 34,
    "ActionGeo_Type": 51, "ActionGeo_FullName": 52, "ActionGeo_CountryCode": 53,
    "ActionGeo_ADM1Code": 54, "ActionGeo_Lat": 56, "ActionGeo_Long": 57,
    "DATEADDED": 59, "SOURCEURL": 60,
}

# CAMEO root codes: 14 protest, 15 force posture, 17 coerce, 18 assault, 19 fight, 20 mass violence
CONFLICT_ROOTS = {"14", "15", "17", "18", "19", "20"}
ROOT_LABEL = {
    "01": "Public statement", "02": "Appeal", "03": "Express intent to cooperate", "04": "Consult",
    "05": "Diplomatic cooperation", "06": "Material cooperation", "07": "Provide aid", "08": "Yield",
    "09": "Investigate", "10": "Demand", "11": "Disapprove", "12": "Reject", "13": "Threaten",
    "14": "Protest", "15": "Exhibit force posture", "16": "Reduce relations", "17": "Coerce",
    "18": "Assault", "19": "Fight", "20": "Mass violence",
}

# GKG column positions (27 columns)
GKG = {"GKGRECORDID": 0, "DATE": 1, "SourceCommonName": 3, "DocumentIdentifier": 4,
       "V2EnhancedThemes": 8, "V2EnhancedLocations": 10, "V2EnhancedPersons": 12,
       "V2EnhancedOrganizations": 14, "V15Tone": 15}


@dataclass
class OsintEvent:
    id: str
    ts: str                      # ISO-8601 UTC, when GDELT added it (15-min bucket)
    lat: float
    lon: float
    place: str
    country: str
    geo_type: int              # 1 country, 2 US state, 3 US city, 4 world city, 5 world state (centroids: 1,2,5)
    actor1: str | None
    actor2: str | None
    actor1_cc: str | None
    actor2_cc: str | None
    event_code: str
    root_code: str
    root_label: str
    quad_class: int
    goldstein: float
    tone: float
    num_mentions: int
    num_sources: int
    url: str
    source_domain: str
    is_conflict: bool
    themes: list[str] = field(default_factory=list)
    persons: list[str] = field(default_factory=list)
    orgs: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def latest_urls() -> dict[str, str]:
    """Return {'export': url, 'mentions': url, 'gkg': url} for the newest 15-min window."""
    r = requests.get(LASTUPDATE, timeout=30)
    r.raise_for_status()
    out = {}
    for line in r.text.strip().splitlines():
        parts = line.split()
        url = parts[-1].replace("http://", "https://")
        if ".export." in url:
            out["export"] = url
        elif ".mentions." in url:
            out["mentions"] = url
        elif ".gkg." in url:
            out["gkg"] = url
    return out


def url_for_window(stamp: str, kind: str) -> str:
    """stamp = 'YYYYMMDDHHMMSS' (15-min aligned). kind in export|mentions|gkg."""
    ext = {"export": "export.CSV.zip", "mentions": "mentions.CSV.zip", "gkg": "gkg.csv.zip"}[kind]
    return f"{BASE}{stamp}.{ext}"


def _download_rows(url: str, cache_dir: Path | None = None) -> list[list[str]]:
    cache = None
    if cache_dir:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache = cache_dir / url.rsplit("/", 1)[-1]
    if cache and cache.exists():
        raw = cache.read_bytes()
    else:
        r = requests.get(url, timeout=120)
        r.raise_for_status()
        raw = r.content
        if cache:
            cache.write_bytes(raw)
    z = zipfile.ZipFile(io.BytesIO(raw))
    text = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
    return [line.split("\t") for line in text.splitlines() if line]


def _domain(url: str) -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    return m.group(1).lower().replace("www.", "") if m else ""


def _stamp_to_iso(stamp: str) -> str:
    return datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).isoformat()


def _split_gkg_list(cell: str) -> list[str]:
    """V2Enhanced* fields are ';'-separated 'Name,offset' pairs."""
    out = []
    for item in (cell or "").split(";"):
        if not item:
            continue
        name = item.split(",", 1)[0].strip()
        if name and name not in out:
            out.append(name)
    return out


def parse_gkg(rows: list[list[str]]) -> dict[str, dict]:
    """Map DocumentIdentifier (URL) -> {themes, persons, orgs}."""
    out = {}
    for r in rows:
        if len(r) < 16:
            continue
        url = r[GKG["DocumentIdentifier"]]
        out[url] = {
            "themes": _split_gkg_list(r[GKG["V2EnhancedThemes"]])[:25],
            "persons": _split_gkg_list(r[GKG["V2EnhancedPersons"]])[:15],
            "orgs": _split_gkg_list(r[GKG["V2EnhancedOrganizations"]])[:15],
        }
    return out


def parse_export(rows: list[list[str]], gkg: dict[str, dict] | None = None,
                 only_geo=True) -> list[OsintEvent]:
    events = []
    for r in rows:
        if len(r) < 61:
            continue
        lat, lon = r[COL["ActionGeo_Lat"]], r[COL["ActionGeo_Long"]]
        if only_geo and (not lat or not lon):
            continue
        try:
            latf, lonf = float(lat), float(lon)
        except ValueError:
            continue
        url = r[COL["SOURCEURL"]]
        root = r[COL["EventRootCode"]]
        extra = (gkg or {}).get(url, {})
        events.append(OsintEvent(
            id="gdelt:" + r[COL["GLOBALEVENTID"]],
            ts=_stamp_to_iso(r[COL["DATEADDED"]]),
            lat=latf, lon=lonf,
            place=r[COL["ActionGeo_FullName"]],
            country=r[COL["ActionGeo_CountryCode"]],
            geo_type=int(r[COL["ActionGeo_Type"]] or 0),
            actor1=r[COL["Actor1Name"]] or None,
            actor2=r[COL["Actor2Name"]] or None,
            actor1_cc=r[COL["Actor1CountryCode"]] or None,
            actor2_cc=r[COL["Actor2CountryCode"]] or None,
            event_code=r[COL["EventCode"]],
            root_code=root,
            root_label=ROOT_LABEL.get(root, root),
            quad_class=int(r[COL["QuadClass"]] or 0),
            goldstein=float(r[COL["GoldsteinScale"]] or 0),
            tone=float(r[COL["AvgTone"]] or 0),
            num_mentions=int(r[COL["NumMentions"]] or 0),
            num_sources=int(r[COL["NumSources"]] or 0),
            url=url,
            source_domain=_domain(url),
            is_conflict=root in CONFLICT_ROOTS,
            themes=extra.get("themes", []),
            persons=extra.get("persons", []),
            orgs=extra.get("orgs", []),
        ))
    return events


def fetch_window(stamp: str | None = None, cache_dir: Path | None = None,
                 with_gkg=True) -> tuple[str, list[OsintEvent]]:
    """Fetch one 15-minute GDELT window. stamp=None -> latest. Returns (stamp, events)."""
    if stamp is None:
        urls = latest_urls()
        stamp = re.search(r"(\d{14})\.export", urls["export"]).group(1)
    else:
        urls = {k: url_for_window(stamp, k) for k in ("export", "mentions", "gkg")}
    log.info("GDELT window %s", stamp)
    gkg = None
    if with_gkg:
        try:
            gkg = parse_gkg(_download_rows(urls["gkg"], cache_dir))
        except Exception as e:  # GKG is ~6 MB; tolerate failure
            log.warning("GKG fetch failed: %s", e)
    events = parse_export(_download_rows(urls["export"], cache_dir), gkg)
    return stamp, events


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    stamp, ev = fetch_window(cache_dir=Path("data/gdelt"))
    conf = [e for e in ev if e.is_conflict]
    print(f"window={stamp} events={len(ev)} conflict={len(conf)}")
    for e in conf[:10]:
        print(f"  {e.root_label:22s} {e.place[:40]:40s} G={e.goldstein:+.1f} {e.source_domain}")
