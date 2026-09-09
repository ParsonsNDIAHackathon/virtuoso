"""Live-mode activity history: backfill the last N hours from sources that keep history, binned at
15 minutes and restricted to the drawn areas of interest, so the live timeline is as informative
as the replay one from the moment the server starts.

  GDELT      every 15-min window of the last N hours (cached zips, export only, no GKG)  -> conflict events
  Telegram   channel previews paged back N hours                                            -> geolocated posts
  FIRMS      already held by FusionState.firms (last 24 h per circle, with novelty)         -> new anomalies
  aircraft / military / correlations                                                        -> our own per-fuse history

Runs in a background thread; the timeline endpoint merges whatever is available.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from .geo import haversine_km
from .ingest_gdelt import fetch_window
from .ingest_telegram import DEFAULT_CHANNELS, fetch_channel

log = logging.getLogger(__name__)
STEP = 15 * 60


def _stamps(hours: float) -> list[str]:
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    now = now - timedelta(minutes=now.minute % 15)
    n = int(hours * 60 // 15)
    return [(now - timedelta(minutes=15 * i)).strftime("%Y%m%d%H%M%S") for i in range(1, n + 1)]


def _in_circles(lat, lon, circles) -> bool:
    if not circles:
        return True
    return any(haversine_km(lat, lon, c["lat"], c["lon"]) <= c.get("radius_nm", 250) * 1.852 for c in circles)


class Backfill:
    def __init__(self, cache_dir, hours: float = 48.0):
        self.cache_dir = cache_dir
        self.hours = hours
        self.lock = threading.Lock()
        self.gdelt: dict[int, int] = {}        # bin_start_epoch -> conflict events inside circles
        self.gdelt_all: dict[int, int] = {}    # bin -> all geocoded events inside circles
        self.social: dict[int, int] = {}       # bin -> geolocated Telegram posts inside circles
        self.done_stamps: set[str] = set()
        self.built_at: float | None = None
        self.progress = "not started"
        self._thread: threading.Thread | None = None

    # ---- GDELT ----
    def _ingest_stamp(self, stamp: str, circles: list[dict]):
        if stamp in self.done_stamps:
            return
        try:
            _, ev = fetch_window(stamp, cache_dir=self.cache_dir, with_gkg=False)
        except Exception as e:
            log.debug("backfill window %s: %s", stamp, e)
            return
        b = int(datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc).timestamp())
        n_all = n_conf = 0
        for e in ev:
            if e.geo_type in (1, 2, 5) or not _in_circles(e.lat, e.lon, circles):
                continue
            n_all += 1
            n_conf += int(e.is_conflict)
        with self.lock:
            self.gdelt[b] = n_conf
            self.gdelt_all[b] = n_all
            self.done_stamps.add(stamp)

    def _ingest_social(self, circles: list[dict]):
        since = (datetime.now(timezone.utc) - timedelta(hours=self.hours)).strftime("%Y-%m-%d")
        counts: dict[int, int] = {}
        for ch in DEFAULT_CHANNELS:
            try:
                for p in fetch_channel(ch, since):
                    if p.lat is None or not _in_circles(p.lat, p.lon, circles):
                        continue
                    t = datetime.fromisoformat(p.ts).timestamp()
                    b = int(t // STEP * STEP)
                    counts[b] = counts.get(b, 0) + 1
            except Exception as e:
                log.warning("backfill telegram %s: %s", ch, e)
        with self.lock:
            self.social = counts

    def run(self, circles: list[dict]):
        t0 = time.time()
        stamps = _stamps(self.hours)
        self.progress = f"GDELT 0/{len(stamps)}"
        for i, s in enumerate(stamps):
            self._ingest_stamp(s, circles)
            if i % 8 == 0:
                self.progress = f"GDELT {i}/{len(stamps)}"
        self.progress = "Telegram"
        self._ingest_social(circles)
        self.built_at = time.time()
        self.progress = "done"
        log.info("backfill: %d GDELT windows, %d social bins in %.0fs", len(self.gdelt), len(self.social), time.time() - t0)

    def start(self, circles: list[dict]):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run, args=(list(circles),), daemon=True)
        self._thread.start()

    def extend(self, circles: list[dict]):
        """Called on the GDELT cadence: pull any windows that became available since the build."""
        for s in _stamps(1.0):
            self._ingest_stamp(s, circles)

    def bins(self, hours: float, history: list[dict], firms: list[dict]) -> list[dict]:
        """Merged 15-min bins for the last `hours`: backfilled flows + own history levels/flows."""
        now = datetime.now(timezone.utc).timestamp()
        start = int((now - hours * 3600) // STEP * STEP)
        n = int((now - start) // STEP) + 1
        out = [{"t": start + i * STEP, "events": 0, "conflict": 0, "social": 0, "tracks": None, "military": None, "ais": None,
                "alerts": 0, "firms_new": 0, "backfilled": False} for i in range(n)]
        idx = {b["t"]: b for b in out}
        with self.lock:
            for t, v in self.gdelt.items():
                if t in idx:
                    idx[t]["conflict"] = v; idx[t]["backfilled"] = True
            for t, v in self.gdelt_all.items():
                if t in idx:
                    idx[t]["events"] = v
            for t, v in self.social.items():
                if t in idx:
                    idx[t]["social"] += v
        for h in firms:
            if h.get("novelty", 0) < 0.9:
                continue
            t = int(datetime.fromisoformat(h["ts"]).timestamp() // STEP * STEP)
            if t in idx:
                idx[t]["firms_new"] += 1
        # own history: levels averaged per bin, flows summed
        acc: dict[int, list] = {}
        for p in history:
            t = int(p["t"] // STEP * STEP)
            if t in idx:
                acc.setdefault(t, []).append(p)
        for t, pts in acc.items():
            b = idx[t]
            ais_levels = [p["ais"] for p in pts if p.get("ais") is not None]
            b["ais"] = round(sum(ais_levels) / len(ais_levels), 1) if ais_levels else None
            b["tracks"] = round(sum(p.get("tracks", 0) for p in pts) / len(pts))
            b["military"] = round(sum(p.get("military", 0) for p in pts) / len(pts))
            b["alerts"] = sum(p.get("d_alerts", 0) for p in pts if p.get("primed"))
            if not b["backfilled"]:
                b["conflict"] = max(b["conflict"], sum(p.get("d_conflict", 0) for p in pts if p.get("primed")))
        return out
