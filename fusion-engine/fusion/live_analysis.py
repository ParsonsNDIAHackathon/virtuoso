"""Live-mode analysis: the same baseline + incident engine the replay uses, over the last 48 hours.

History available to a running engine:
  news / conflict   GDELT windows for the last 48 h, re-read from the on-disk window cache the
                    backfill already populated (no network once cached)
  social            the geolocated posts the pipeline keeps (6 h) plus the current window
  firms_new         thermal detections the pipeline keeps (24 h)
  tracks / military / navint   the pipeline's rolling aircraft history (2 h), so these streams
                    have adequate reference only for recent bins and report "insufficient"
                    elsewhere. That is stated on every score rather than hidden.

The build runs in a background thread on a 15-minute cadence and never blocks a fuse.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from .backfill import _stamps
from .baseline import Baseline
from .incidents import IncidentTracker
from .ingest_gdelt import fetch_window

log = logging.getLogger(__name__)
HOURS = 48.0


def _tracks_to_archive(track_history) -> dict[str, dict]:
    """AirTrack snapshots -> the replay archive layout the baseline reads (10-field points)."""
    out: dict[str, dict] = {}
    for tr in track_history:
        t = datetime.fromisoformat(tr.ts).timestamp()
        a = out.setdefault(tr.hex, {"hex": tr.hex, "military": False, "points": []})
        a["military"] = a["military"] or bool(tr.military)
        a["points"].append([t, tr.lat, tr.lon, tr.alt_ft, tr.gs_kt, tr.track_deg, tr.callsign, tr.source,
                            getattr(tr, "nic", None), getattr(tr, "nac_p", None)])
    for a in out.values():
        a["points"].sort(key=lambda p: p[0])
    return out


class LiveAnalysis:
    def __init__(self, cache_dir, cadence_s: float = 900.0):
        self.cache_dir = cache_dir
        self.cadence_s = cadence_s
        self.lock = threading.Lock()
        self.baseline: Baseline | None = None
        self.tracker: IncidentTracker | None = None
        self.built_at: float | None = None
        self.progress = "not started"
        self._thread: threading.Thread | None = None
        self._events_cache: dict[str, list] = {}

    def due(self) -> bool:
        return self.built_at is None or time.time() - self.built_at >= self.cadence_s

    def start(self, events, social, firms, track_history) -> bool:
        """Kick off a build if one is due and none is running. Returns True if started."""
        if self._thread and self._thread.is_alive():
            return False
        if not self.due():
            return False
        snapshot = (list(events), list(social), list(firms), list(track_history))
        self._thread = threading.Thread(target=self._build, args=snapshot, name="live-analysis", daemon=True)
        self._thread.start()
        return True

    def _history_events(self) -> list:
        """Every geocoded GDELT event in the last 48 h from the cached windows (cache-only reads)."""
        out = []
        stamps = _stamps(HOURS)
        keep = set(stamps)
        for stale in [s for s in self._events_cache if s not in keep]:
            self._events_cache.pop(stale, None)
        missing = 0
        for i, stamp in enumerate(stamps):
            if stamp not in self._events_cache:
                try:
                    _, ev = fetch_window(stamp, cache_dir=self.cache_dir, with_gkg=False)
                    self._events_cache[stamp] = [e for e in ev if e.geo_type not in (1, 2, 5)]
                except Exception:
                    missing += 1
                    self._events_cache[stamp] = []
            out.extend(self._events_cache[stamp])
            if i % 16 == 0:
                self.progress = f"reading GDELT history {i}/{len(stamps)}"
        if missing:
            log.info("live analysis: %d of %d GDELT windows unavailable", missing, len(stamps))
        return out

    def _build(self, events, social, firms, track_history):
        t0 = time.time()
        try:
            now = datetime.now(timezone.utc).timestamp()
            t_max = int(now // 3600 * 3600) + 3600 - 1          # end of the current hour
            t_min = t_max + 1 - int(HOURS * 3600)
            history = self._history_events()
            seen = {e.id for e in history}
            merged = history + [e for e in list(events) + list(social) if e.id not in seen]
            b = Baseline(t_min, t_max)
            b.add_events(merged)
            b.add_tracks(_tracks_to_archive(track_history))
            b.add_firms(firms)
            self.progress = "forming incidents"
            tracker = IncidentTracker(b, merged)
            with self.lock:
                self.baseline, self.tracker, self.built_at = b, tracker, time.time()
            self.progress = "done"
            log.info("live analysis: %d events, %d cells, %d incidents now, %.0fs",
                     len(merged), len(b.cells()), len(tracker.at(now)), time.time() - t0)
        except Exception as e:
            self.progress = f"failed: {str(e)[:120]}"
            log.exception("live analysis build failed")

    def snapshot(self) -> dict:
        now = datetime.now(timezone.utc).timestamp()
        with self.lock:
            b, tracker, built = self.baseline, self.tracker, self.built_at
        if not b or not tracker:
            return {"status": self.progress, "built_at": None, "incidents": [], "departures": [], "departed_cells": []}
        return {
            "status": self.progress,
            "built_at": datetime.fromtimestamp(built, tz=timezone.utc).isoformat() if built else None,
            "t": now,
            "window": {"t_min": b.t_min, "t_max": b.t_max, "hours": HOURS},
            "baseline": {"z_threshold": 2.0, "reference": "same hour +/-2 h on the prior day, 2-3 h away today",
                         "days": 2, "note": "aircraft streams have 2 h of history; they read insufficient outside it"},
            "incidents": [inc.to_dict() for inc in tracker.at(now)],
            "departures": [d.to_dict() for d in b.departures_at(now)],
            "departed_cells": sorted([list(c) for c in b.departed_cells(now)]),
            "timeline_z": b.timeline(),
            "t_min": b.t_min, "step_s": b.step,
        }
