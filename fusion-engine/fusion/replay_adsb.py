"""Historical ADS-B replay from the adsb.lol globe_history daily archives.

Archive: https://github.com/adsblol/globe_history_2026/releases  (tag vYYYY.MM.DD-planes-readsb-prod-0,
two split tar parts .tar.aa/.tar.ab, ODbL). Inside: traces/<xx>/trace_full_<hex>.json, each a gzip'd
readsb trace json (see https://github.com/wiedehopf/readsb/blob/dev/README-json.md#trace-jsons).

Step 1 (once, slow, ~4 GB in):   python -m fusion.replay_adsb extract C:/dev/ndia_raw/adsb_2026-08-18 --bbox 23.5,52,28.5,59
Step 2 (fast, at runtime):        tracks = load_tracks(json); snapshot_at(tracks, t_epoch)
"""
from __future__ import annotations

import argparse
import gzip
import io
import json
import logging
import re
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

from .ingest_adsb import AirTrack

log = logging.getLogger(__name__)

HORMUZ_BBOX = (23.5, 52.0, 28.5, 59.0)


class _Concat(io.RawIOBase):
    """Read several split files as one stream (cat a b | tar)."""

    def __init__(self, paths):
        self.paths = list(paths)
        self.i = 0
        self.f = open(self.paths[0], "rb")

    def readable(self):
        return True

    def readinto(self, b):
        while self.f is not None:
            n = self.f.readinto(b)
            if n:
                return n
            self.f.close()
            self.i += 1
            self.f = open(self.paths[self.i], "rb") if self.i < len(self.paths) else None
        return 0


def _bbox_regex(bbox):
    """Cheap byte-level prefilter: any ',lat,lon,' pair with integer parts inside the box."""
    la0, lo0, la1, lo1 = bbox
    lats = "|".join(str(i) for i in range(int(la0), int(la1) + 1))
    lons = "|".join(str(i) for i in range(int(lo0), int(lo1) + 1))
    return re.compile(rb",\s*(?:%s)\.\d+,\s*(?:%s)\.\d+," % (lats.encode(), lons.encode()))


def extract_bbox(archive_dir: Path, bbox=HORMUZ_BBOX, out: Path | None = None,
                 margin_deg: float = 0.5, max_members: int | None = None) -> dict:
    """Stream the split tar, keep aircraft with any position inside bbox (+margin). Returns
    {hex: {meta..., points: [[t_epoch, lat, lon, alt_ft|None|'ground', gs, track, flight, source], ...]}}."""
    parts = sorted(p for p in Path(archive_dir).iterdir() if re.search(r"\.tar\.[a-z]{2}$", p.name))
    if not parts:
        raise FileNotFoundError(f"no .tar.* parts in {archive_dir}")
    la0, lo0, la1, lo1 = bbox
    la0m, lo0m, la1m, lo1m = la0 - margin_deg, lo0 - margin_deg, la1 + margin_deg, lo1 + margin_deg
    pre = _bbox_regex((la0m, lo0m, la1m, lo1m))
    kept: dict[str, dict] = {}
    n = n_trace = n_pre = 0
    t0 = time.time()
    stream = _Concat(parts)
    try:
        tf = tarfile.open(fileobj=io.BufferedReader(stream, 1 << 20), mode="r|")
        for m in tf:
            n += 1
            if max_members and n > max_members:
                break
            if not m.isfile() or "/traces/" not in m.name and not m.name.startswith("traces/"):
                continue
            base = m.name.rsplit("/", 1)[-1]
            if not base.startswith("trace_full_"):
                continue
            n_trace += 1
            raw = tf.extractfile(m).read()
            try:
                data = gzip.decompress(raw)
            except OSError:
                data = raw                         # some archives store plain json
            if not pre.search(data):
                continue
            n_pre += 1
            try:
                j = json.loads(data)
            except Exception:
                continue
            base_ts = float(j.get("timestamp", 0))
            pts = []
            flight = None
            for p in j.get("trace", []):
                lat, lon = p[1], p[2]
                if lat is None or lon is None:
                    continue
                if not (la0m <= lat <= la1m and lo0m <= lon <= lo1m):
                    continue
                ac = p[8] if len(p) > 8 and isinstance(p[8], dict) else None
                if ac and ac.get("flight"):
                    flight = ac["flight"].strip()
                src = p[9] if len(p) > 9 else None
                pts.append([round(base_ts + p[0], 1), lat, lon, p[3], p[4], p[5], flight, src])
            if not pts:
                continue
            hexid = j.get("icao") or base[len("trace_full_"):-5]
            kept[hexid] = {
                "hex": hexid, "r": j.get("r"), "t": j.get("t"), "desc": j.get("desc"),
                "military": bool(j.get("dbFlags", 0) & 1), "ownop": j.get("ownOp"),
                "n": len(pts), "t_first": pts[0][0], "t_last": pts[-1][0], "points": pts,
            }
            if len(kept) % 50 == 0:
                log.info("%d members, %d traces, %d prefilter hits, %d kept, %.0fs",
                         n, n_trace, n_pre, len(kept), time.time() - t0)
    except (tarfile.ReadError, EOFError) as e:
        log.warning("archive ended early (partial download?): %s", e)
    log.info("done: %d members, %d traces, %d kept aircraft in bbox, %.0fs", n, n_trace, len(kept), time.time() - t0)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"bbox": bbox, "source": "adsb.lol globe_history (ODbL)",
                                   "aircraft": kept}), encoding="utf-8")
        log.info("wrote %s (%.1f MB)", out, out.stat().st_size / 1e6)
    return kept


def load_tracks(path: Path) -> dict[str, dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["aircraft"]


def snapshot_at(tracks: dict[str, dict], t_epoch: float, max_age_s: float = 300.0) -> list[AirTrack]:
    """Latest position per aircraft at or before t_epoch (within max_age_s) as AirTrack objects."""
    out = []
    ts_iso = datetime.fromtimestamp(t_epoch, tz=timezone.utc).isoformat()
    for hexid, a in tracks.items():
        pts = a["points"]
        if pts[0][0] > t_epoch or pts[-1][0] < t_epoch - max_age_s:
            continue
        # binary search last point <= t
        lo, hi = 0, len(pts) - 1
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if pts[mid][0] <= t_epoch:
                lo = mid
            else:
                hi = mid - 1
        p = pts[lo]
        if p[0] < t_epoch - max_age_s:
            continue
        alt = p[3]
        on_ground = alt == "ground"
        out.append(AirTrack(
            id=f"adsb:{hexid}", hex=hexid, ts=ts_iso, lat=p[1], lon=p[2],
            callsign=p[6], registration=a.get("r"), ac_type=a.get("t"),
            alt_ft=None if on_ground or alt is None else int(alt), on_ground=on_ground,
            gs_kt=p[4], track_deg=p[5], squawk=None, emergency=None, category=None,
            military=a.get("military", False), source=p[7] or "archive", rssi=None, messages=None,
        ))
    return out


def track_polylines(tracks: dict[str, dict], t_from: float, t_to: float, max_points=400) -> list[dict]:
    """Per-aircraft polylines within a time range, for drawing history tails on the map."""
    out = []
    for hexid, a in tracks.items():
        pts = [p for p in a["points"] if t_from <= p[0] <= t_to]
        if len(pts) < 2:
            continue
        step = max(1, len(pts) // max_points)
        out.append({"hex": hexid, "military": a.get("military", False), "t": a.get("t"), "r": a.get("r"),
                    "callsign": next((p[6] for p in reversed(pts) if p[6]), None),
                    "coords": [[p[1], p[2]] for p in pts[::step]]})
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
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract")
    ex.add_argument("archive_dir")
    ex.add_argument("--bbox", default=",".join(map(str, HORMUZ_BBOX)))
    ex.add_argument("--out", default=None)
    ex.add_argument("--max-members", type=int, default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    root = _data_root().parent  # data dir parent; see _data_root()
    bbox = tuple(float(x) for x in a.bbox.split(","))
    day = re.search(r"(\d{4}-\d{2}-\d{2})", a.archive_dir)
    out = Path(a.out) if a.out else _data_root() / "replay" / f"{day.group(1) if day else 'archive'}_adsb.json"
    kept = extract_bbox(Path(a.archive_dir), bbox, out, max_members=a.max_members)
    mil = sum(1 for v in kept.values() if v["military"])
    print(f"{len(kept)} aircraft in bbox ({mil} military) -> {out}")
    for v in sorted(kept.values(), key=lambda v: -v["n"])[:15]:
        print(f"  {v['hex']} {v.get('r') or '-':10s} {v.get('t') or '-':6s} mil={v['military']} pts={v['n']}")
