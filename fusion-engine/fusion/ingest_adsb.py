"""ADS-B ingest from adsb.lol (open community aggregator, no key required).

API docs: https://api.adsb.lol/docs
  /v2/mil                       all aircraft flagged military
  /v2/point/{lat}/{lon}/{nm}    aircraft within radius (nautical miles, max 250)
  /v2/hex/{icao}                one aircraft by ICAO hex
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)
BASE = "https://api.adsb.lol/v2/"
HEADERS = {"User-Agent": "ParsonsOfInterest-MultiINT/0.1 (NDIA hackathon)"}


@dataclass
class AirTrack:
    id: str            # "adsb:<hex>"
    hex: str
    ts: str            # ISO-8601 UTC of snapshot
    lat: float
    lon: float
    callsign: str | None
    registration: str | None
    ac_type: str | None
    alt_ft: int | None     # None when on ground / unknown
    on_ground: bool
    gs_kt: float | None
    track_deg: float | None
    squawk: str | None
    emergency: str | None
    category: str | None
    military: bool
    source: str        # adsb_icao | mlat | tisb | adsr ...
    rssi: float | None
    messages: int | None
    nic: int | None = None       # navigation integrity category, as transmitted
    nac_p: int | None = None     # navigation accuracy category (position), as transmitted

    def to_dict(self):
        return asdict(self)


def _norm(a: dict, ts: str, military: bool) -> AirTrack | None:
    if a.get("lat") is None or a.get("lon") is None:
        return None
    alt = a.get("alt_baro")
    on_ground = alt == "ground"
    return AirTrack(
        id=f"adsb:{a['hex']}",
        hex=a["hex"],
        ts=ts,
        lat=float(a["lat"]), lon=float(a["lon"]),
        callsign=(a.get("flight") or "").strip() or None,
        registration=a.get("r"),
        ac_type=a.get("t"),
        alt_ft=None if on_ground or alt is None else int(alt),
        on_ground=on_ground,
        gs_kt=a.get("gs"),
        track_deg=a.get("track"),
        squawk=a.get("squawk"),
        emergency=(a.get("emergency") if a.get("emergency") not in (None, "none") else None),
        category=a.get("category"),
        military=military or bool(a.get("dbFlags", 0) & 1),
        source=a.get("type", "unknown"),
        rssi=a.get("rssi"),
        messages=a.get("messages"),
        nic=a.get("nic"),
        nac_p=a.get("nac_p"),
    )


MIN_GAP_S = 3.0          # adsb.lol rate-limits bursts; ~3 s between calls avoids most 429s
_last_call = 0.0


def _get(path: str, retries: int = 3) -> dict:
    """GET with polite spacing and exponential backoff on 429/5xx."""
    global _last_call
    for attempt in range(retries + 1):
        gap = MIN_GAP_S - (time.monotonic() - _last_call)
        if gap > 0:
            time.sleep(gap)
        r = requests.get(BASE + path, headers=HEADERS, timeout=30)
        _last_call = time.monotonic()
        if r.status_code == 429 or r.status_code >= 500:
            if attempt == retries:
                r.raise_for_status()
            wait = 3.0 * (2 ** attempt)
            log.warning("adsb.lol %s -> %s, retrying in %.0fs", path, r.status_code, wait)
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("unreachable")


def fetch_military() -> list[AirTrack]:
    d = _get("mil")
    ts = datetime.fromtimestamp(d["now"] / 1000, tz=timezone.utc).isoformat()
    out = [t for t in (_norm(a, ts, True) for a in d.get("ac", [])) if t]
    log.info("adsb.lol /mil: %d tracks with position", len(out))
    return out


def fetch_point(lat: float, lon: float, radius_nm: int = 250) -> list[AirTrack]:
    d = _get(f"point/{lat}/{lon}/{min(radius_nm, 250)}")
    ts = datetime.fromtimestamp(d["now"] / 1000, tz=timezone.utc).isoformat()
    out = [t for t in (_norm(a, ts, False) for a in d.get("ac", [])) if t]
    log.info("adsb.lol point(%s,%s,%snm): %d tracks", lat, lon, radius_nm, len(out))
    return out


def fetch_regions(circles: list[tuple], radius_nm=250) -> list[AirTrack]:
    """Union of point queries, de-duplicated by hex. Each circle is (lat, lon) or (lat, lon, radius_nm)."""
    seen: dict[str, AirTrack] = {}
    for c in circles:
        lat, lon = c[0], c[1]
        r = int(c[2]) if len(c) > 2 and c[2] else radius_nm
        try:
            for t in fetch_point(lat, lon, r):
                seen[t.hex] = t
        except Exception as e:
            log.warning("point query %s,%s failed: %s", lat, lon, e)
    return list(seen.values())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mil = fetch_military()
    print(f"{len(mil)} military tracks")
    for t in mil[:10]:
        print(f"  {t.hex} {t.callsign or '-':9s} {t.ac_type or '-':5s} {t.lat:7.2f},{t.lon:8.2f} alt={t.alt_ft}")
