"""NASA FIRMS active-fire / thermal-anomaly ingest (VIIRS SNPP + NOAA-20/21, 375 m).

API: https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{bbox}/{days}/{date}
Key: FIRMS_MAP_KEY in fusion-engine/.env (free, https://firms.modaps.eosdis.nasa.gov/api/map_key/).
Thermal anomalies in the Gulf are dominated by gas flares; the useful signal is a *new* hotspot
where none exists on a baseline day (strike site, burning vessel, fuel farm). `novelty()` scores that.
"""
from __future__ import annotations

import csv
import io
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import requests

log = logging.getLogger(__name__)
BASE = "https://firms.modaps.eosdis.nasa.gov/api/area/csv"
SOURCES = ("VIIRS_SNPP_NRT", "VIIRS_NOAA20_NRT", "VIIRS_NOAA21_NRT")


def _key() -> str:
    k = os.environ.get("FIRMS_MAP_KEY")
    if not k:
        env = Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("FIRMS_MAP_KEY="):
                    k = line.split("=", 1)[1].strip()
    if not k:
        raise RuntimeError("FIRMS_MAP_KEY not set (env or fusion-engine/.env)")
    return k


@dataclass
class Hotspot:
    id: str
    ts: str
    lat: float
    lon: float
    satellite: str
    frp: float          # fire radiative power, MW
    bright: float       # brightness temp I-4, K
    confidence: str     # l/n/h
    daynight: str
    novelty: float = 0.0   # 0..1, distance-based novelty vs baseline

    def to_dict(self):
        return asdict(self)


def fetch(bbox, day: str | None = None, days: int = 1, sources=SOURCES) -> list[Hotspot]:
    """bbox = (lat_min, lon_min, lat_max, lon_max). day=None -> most recent `days`."""
    la0, lo0, la1, lo1 = bbox
    key = _key()
    out: list[Hotspot] = []
    for src in sources:
        url = f"{BASE}/{key}/{src}/{lo0},{la0},{lo1},{la1}/{days}" + (f"/{day}" if day else "")
        r = requests.get(url, timeout=60)
        if r.status_code != 200 or r.text.startswith("Invalid"):
            log.warning("FIRMS %s: %s %s", src, r.status_code, r.text[:80])
            continue
        for row in csv.DictReader(io.StringIO(r.text)):
            t = row["acq_time"].zfill(4)
            ts = datetime.strptime(row["acq_date"] + t, "%Y-%m-%d%H%M").replace(tzinfo=timezone.utc).isoformat()
            out.append(Hotspot(
                id=f"firms:{row['satellite']}:{row['acq_date']}{t}:{row['latitude']},{row['longitude']}",
                ts=ts, lat=float(row["latitude"]), lon=float(row["longitude"]),
                satellite=row["satellite"], frp=float(row.get("frp") or 0), bright=float(row.get("bright_ti4") or 0),
                confidence=row.get("confidence", "n"), daynight=row.get("daynight", "?"),
            ))
    log.info("FIRMS %s: %d hotspots in bbox", day or "latest", len(out))
    return out


def novelty(hotspots: list[Hotspot], baseline: list[Hotspot], radius_km: float = 2.0) -> list[Hotspot]:
    """Score each hotspot by whether any baseline hotspot lies within radius_km (0 = routine flare)."""
    from .geo import haversine_km
    for h in hotspots:
        near = min((haversine_km(h.lat, h.lon, b.lat, b.lon) for b in baseline), default=999.0)
        h.novelty = round(min(1.0, near / radius_km), 3) if baseline else 0.5
    return hotspots



def _data_root():
    """data dir shared with the server: FUSION_DATA_DIR (from env or fusion-engine/.env), else ./data"""
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass
    return Path(os.getenv("FUSION_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser()
    ap.add_argument("day")
    ap.add_argument("--baseline", default=None, help="baseline start day for novelty (2-day window); default = day-2")
    ap.add_argument("--bbox", default="23.5,52,28.5,59")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    bbox = tuple(float(x) for x in a.bbox.split(","))
    hs = fetch(bbox, a.day)
    from datetime import timedelta
    base_day = a.baseline or (datetime.strptime(a.day, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
    hs = novelty(hs, fetch(bbox, base_day, days=2))
    root = _data_root().parent  # data dir parent; see _data_root()
    out = _data_root() / "replay" / f"{a.day}_firms.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([h.to_dict() for h in hs]), encoding="utf-8")
    novel = sorted(hs, key=lambda h: -h.novelty)[:10]
    print(f"{len(hs)} hotspots -> {out}")
    for h in novel:
        print(f"  novelty={h.novelty:.2f} {h.ts[11:16]} {h.lat:.3f},{h.lon:.3f} frp={h.frp:.0f}MW {h.satellite} {h.daynight}")
