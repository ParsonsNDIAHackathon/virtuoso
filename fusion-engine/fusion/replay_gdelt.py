"""Historical GDELT replay: load every 15-minute window of a UTC day, filtered to a bounding box
and/or keyword list, from the local cache (downloading what is missing).

    python -m fusion.replay_gdelt 2026-08-18 --bbox 23.5,52,28.5,59 --kw hormuz,tanker
"""
from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from .ingest_gdelt import OsintEvent, _download_rows, parse_export, parse_gkg, url_for_window

log = logging.getLogger(__name__)

# Strait of Hormuz / Gulf of Oman / southern Persian Gulf
HORMUZ_BBOX = (23.5, 52.0, 28.5, 59.0)          # lat_min, lon_min, lat_max, lon_max
HORMUZ_KW = ("hormuz", "tanker", "persian gulf", "gulf of oman", "fujairah", "bandar abbas", "irgc")


def day_stamps(day: str) -> list[str]:
    d = datetime.strptime(day, "%Y-%m-%d")
    return [(d + timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S") for i in range(96)]


def in_bbox(e: OsintEvent, bbox) -> bool:
    la0, lo0, la1, lo1 = bbox
    return la0 <= e.lat <= la1 and lo0 <= e.lon <= lo1


def load_day(day: str, cache_dir: Path, bbox=None, keywords=(), with_gkg=True,
             drop_centroids=True) -> list[OsintEvent]:
    """Return geocoded events for one UTC day. Keep an event if it is inside bbox OR its source URL
    matches a keyword. Country/state centroids are dropped unless drop_centroids=False."""
    kw_re = re.compile("|".join(re.escape(k) for k in keywords), re.I) if keywords else None
    out: list[OsintEvent] = []
    for s in day_stamps(day):
        try:
            gkg = parse_gkg(_download_rows(url_for_window(s, "gkg"), cache_dir)) if with_gkg else None
        except Exception as e:
            log.warning("gkg %s: %s", s, e)
            gkg = None
        try:
            rows = _download_rows(url_for_window(s, "export"), cache_dir)
        except Exception as e:
            log.warning("export %s: %s", s, e)
            continue
        for ev in parse_export(rows, gkg):
            if drop_centroids and ev.geo_type in (1, 2, 5):
                continue
            hit = (bbox is not None and in_bbox(ev, bbox)) or (kw_re is not None and kw_re.search(ev.url))
            if hit:
                out.append(ev)
    log.info("GDELT replay %s: %d events (%d conflict)", day, len(out), sum(e.is_conflict for e in out))
    return out



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
    ap = argparse.ArgumentParser()
    ap.add_argument("day")
    ap.add_argument("--bbox", default=",".join(map(str, HORMUZ_BBOX)))
    ap.add_argument("--kw", default=",".join(HORMUZ_KW))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    root = _data_root().parent  # data dir parent; see _data_root()
    bbox = tuple(float(x) for x in a.bbox.split(","))
    evs = load_day(a.day, _data_root() / "gdelt", bbox, tuple(k for k in a.kw.split(",") if k))
    out = Path(a.out) if a.out else _data_root() / "replay" / f"{a.day}_gdelt.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([e.to_dict() for e in evs]), encoding="utf-8")
    print(f"{len(evs)} events -> {out}")
    from collections import Counter
    print(Counter(e.root_label for e in evs).most_common(8))
