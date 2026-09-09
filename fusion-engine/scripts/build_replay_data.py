#!/usr/bin/env python3
"""Build replay assets independently of the API/dashboard process.

Examples:
  python scripts/build_replay_data.py 2026-08-17 2026-08-18
  python scripts/build_replay_data.py 2026-08-18 --download-adsb /tmp/adsb-archives
  python scripts/build_replay_data.py 2026-08-18 --adsb-archive /data/adsb_2026-08-18

GDELT and Telegram are keyless. FIRMS requires FIRMS_MAP_KEY. Historical ADS-B requires the
adsb.lol daily archive; pass an existing directory or opt into the large resumable download.
The script never starts or imports the web application.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fusion.ingest_firms import fetch as fetch_firms, novelty as firms_novelty  # noqa: E402
from fusion.ingest_telegram import DEFAULT_CHANNELS, fetch_channel  # noqa: E402
from fusion.replay_adsb import extract_bbox  # noqa: E402
from fusion.replay_gdelt import HORMUZ_KW, load_day  # noqa: E402

log = logging.getLogger("replay-builder")


def _write(path: Path, values) -> dict:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(values, ensure_ascii=False).encode()
    path.write_bytes(raw)
    return {"file": path.name, "records": len(values), "bytes": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def _archive_for(base: Path, day: str) -> Path:
    candidate = base / f"adsb_{day}"
    return candidate if candidate.exists() else base


def build_day(day: str, args) -> dict:
    bbox = tuple(float(value) for value in args.bbox.split(","))
    out = args.out_dir
    manifest: dict = {"day": day, "bbox": bbox, "created_by": "scripts/build_replay_data.py", "layers": {}, "errors": {}}

    tasks = {value.strip() for value in args.layers.split(",") if value.strip()}
    gpath = out / f"{day}_gdelt.json"
    if "gdelt" in tasks and (args.force or not gpath.exists()):
        try:
            events = load_day(day, args.gdelt_cache, bbox, tuple(args.keywords or HORMUZ_KW))
            manifest["layers"]["gdelt"] = _write(gpath, [event.to_dict() for event in events])
        except Exception as error:
            manifest["errors"]["gdelt"] = str(error)
            log.exception("%s GDELT failed", day)
    elif gpath.exists():
        manifest["layers"]["gdelt"] = {"file": gpath.name, "status": "already present"}

    tpath = out / f"{day}_telegram.json"
    if "telegram" in tasks and (args.force or not tpath.exists()):
        try:
            until = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
            posts = []
            for channel in args.channels:
                posts.extend(fetch_channel(channel, day, until))
            unique = {post.id: post for post in posts}
            manifest["layers"]["telegram"] = _write(tpath, [post.to_dict() for post in sorted(unique.values(), key=lambda value: value.ts)])
        except Exception as error:
            manifest["errors"]["telegram"] = str(error)
            log.exception("%s Telegram failed", day)
    elif tpath.exists():
        manifest["layers"]["telegram"] = {"file": tpath.name, "status": "already present"}

    fpath = out / f"{day}_firms.json"
    if "firms" in tasks and (args.force or not fpath.exists()):
        try:
            baseline = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=2)).strftime("%Y-%m-%d")
            hotspots = firms_novelty(fetch_firms(bbox, day), fetch_firms(bbox, baseline, days=2))
            manifest["layers"]["firms"] = _write(fpath, [hotspot.to_dict() for hotspot in hotspots])
        except Exception as error:
            manifest["errors"]["firms"] = str(error)
            log.exception("%s FIRMS failed", day)
    elif fpath.exists():
        manifest["layers"]["firms"] = {"file": fpath.name, "status": "already present"}

    apath = out / f"{day}_adsb.json"
    if "adsb" in tasks and (args.force or not apath.exists()):
        try:
            archive = _archive_for(args.adsb_archive, day) if args.adsb_archive else None
            if args.download_adsb:
                archive = Path(args.download_adsb) / f"adsb_{day}"
                archive.mkdir(parents=True, exist_ok=True)
                subprocess.run([
                    sys.executable, str(ROOT / "scripts" / "fetch_archive.py"), day, str(archive),
                    "--threads", str(args.threads),
                ], check=True)
            if not archive:
                raise RuntimeError("pass --adsb-archive or --download-adsb to build historical ADS-B")
            tracks = extract_bbox(archive, bbox=bbox, out=apath)
            manifest["layers"]["adsb"] = {"file": apath.name, "records": len(tracks), "bytes": apath.stat().st_size}
        except Exception as error:
            manifest["errors"]["adsb"] = str(error)
            log.exception("%s ADS-B failed", day)
    elif apath.exists():
        manifest["layers"]["adsb"] = {"file": apath.name, "status": "already present"}

    mpath = out / f"{day}_manifest.json"
    mpath.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    if args.strict and manifest["errors"]:
        raise RuntimeError(f"{day} failed layers: {', '.join(manifest['errors'])}")
    return manifest


def main():
    parser = argparse.ArgumentParser(description="Build standalone multi-source replay files")
    parser.add_argument("days", nargs="+", help="YYYY-MM-DD day(s)")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "data" / "replay")
    parser.add_argument("--gdelt-cache", type=Path, default=ROOT / "data" / "gdelt")
    parser.add_argument("--bbox", default="23.5,52,28.5,59", help="lat_min,lon_min,lat_max,lon_max")
    parser.add_argument("--channels", nargs="+", default=DEFAULT_CHANNELS)
    parser.add_argument("--keywords", nargs="*", default=None)
    parser.add_argument("--layers", default="gdelt,telegram,firms,adsb")
    parser.add_argument("--adsb-archive", type=Path, help="existing archive directory; may contain adsb_<day> subdirs")
    parser.add_argument("--download-adsb", type=Path, help="download the large daily archives below this directory")
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--force", action="store_true", help="rebuild files that already exist")
    parser.add_argument("--strict", action="store_true", help="exit nonzero if any requested layer fails")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    summaries = [build_day(day, args) for day in args.days]
    print(json.dumps(summaries, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
