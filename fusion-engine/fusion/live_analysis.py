"""Live-mode analysis: the same baseline + incident engine the replay uses, over the last 48 hours.

History available to a running engine:
  news / conflict   GDELT windows for the last 48 h, re-read from the on-disk window cache the
                    backfill already populated (no network once cached)
  social            the geolocated posts the pipeline keeps (6 h) plus the current window
  firms_new         thermal detections the pipeline keeps (24 h)
  tracks / military / navint   the pipeline's rolling aircraft history (2 h), so these streams
                    have adequate reference only for recent bins and report "insufficient"
                    elsewhere. That is stated on every score rather than hidden.

The build runs in a background thread on a 15-minute cadence and never blocks a fuse. It is
limited to the drawn areas of interest (the same rule the timeline backfill uses), so the cell count
stays in the tens rather than the thousands GDELT geocodes worldwide.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from datetime import datetime, timezone

from .backfill import _in_circles, _stamps
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
    def __init__(self, cache_dir, cadence_s: float = 900.0, aircraft_log=None, state_path=None):
        self.cache_dir = cache_dir
        self.aircraft_log = aircraft_log
        self.state_path = state_path
        self._id_map: dict[str, str] = {}          # tracker id -> durable id
        self._durable: dict[str, dict] = {}        # durable id -> {cells, first_t, last_t, revisions}
        self._seq = 0
        self._load_state()
        self.cadence_s = cadence_s
        self.lock = threading.Lock()
        self.baseline: Baseline | None = None
        self.tracker: IncidentTracker | None = None
        self.built_at: float | None = None
        self.progress = "not started"
        self._thread: threading.Thread | None = None
        self._events_cache: dict[str, list] = {}

    def _load_state(self):
        if not self.state_path:
            return
        try:
            st = json.loads(Path(self.state_path).read_text(encoding="utf-8"))
            self._seq = int(st.get("seq", 0))
            self._durable = dict(st.get("incidents", {}))
        except FileNotFoundError:
            pass
        except Exception as e:
            log.warning("incident state unreadable: %s", e)

    def _save_state(self):
        if not self.state_path:
            return
        try:
            Path(self.state_path).write_text(json.dumps({"seq": self._seq, "incidents": self._durable}), encoding="utf-8")
        except Exception as e:
            log.warning("incident state not saved: %s", e)

    def _assign_durable_ids(self, incidents, now: float):
        """Match this build's incidents to durable ones by cell overlap; keep first_t and revisions."""
        unused = dict(self._durable)
        new_map, new_durable = {}, {}
        for inc in incidents:
            cells = {tuple(c) for c in inc.cells}
            neigh = {(c[0] + dy, c[1] + dx) for c in cells for dy in (-1, 0, 1) for dx in (-1, 0, 1)}
            match = next((k for k, v in unused.items()
                          if {tuple(c) for c in v["cells"]} & neigh and now - v.get("last_t", now) <= 6 * 3600), None)
            if match:
                unused.pop(match)
                did = match
                prev = self._durable[did]
                inc.first_t = min(inc.first_t, prev.get("first_t", inc.first_t))
                first_new = inc.revisions[0]["t"] if inc.revisions else now
                inc.revisions = [r for r in prev.get("revisions", []) if r["t"] < first_new] + inc.revisions
            else:
                self._seq += 1
                did = f"incident:{self._seq:04d}"
            new_map[inc.id] = did
            new_durable[did] = {"cells": inc.cells, "first_t": inc.first_t, "last_t": now, "revisions": inc.revisions[-24:]}
        for k, v in unused.items():          # keep recently closed incidents so one quiet build does not rename them
            if now - v.get("last_t", 0) <= 6 * 3600:
                new_durable[k] = v
        self._id_map, self._durable = new_map, new_durable
        self._save_state()

    def due(self) -> bool:
        return self.built_at is None or time.time() - self.built_at >= self.cadence_s

    def start(self, events, social, firms, track_history, circles=None) -> bool:
        """Kick off a build if one is due and none is running. Returns True if started."""
        if self._thread and self._thread.is_alive():
            return False
        if not self.due():
            return False
        snapshot = (list(events), list(social), list(firms), list(track_history), list(circles or []))
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
                self.progress = f"loading news history {i}/{len(stamps)} windows"
        if missing:
            log.info("live analysis: %d of %d GDELT windows unavailable", missing, len(stamps))
        return out

    def _build(self, events, social, firms, track_history, circles):
        t0 = time.time()
        snapshot_taken = t0          # inputs were captured at start; assessments are as of this instant
        try:
            inside = (lambda lat, lon: _in_circles(lat, lon, circles))
            now = datetime.now(timezone.utc).timestamp()
            t_max = int(now // 3600 * 3600) + 3600 - 1          # end of the current hour
            t_min = t_max + 1 - int(HOURS * 3600)
            history = self._history_events()
            seen = {e.id for e in history}
            merged = [e for e in history + [e for e in list(events) + list(social) if e.id not in seen] if inside(e.lat, e.lon)]
            b = Baseline(t_min, t_max)
            b.add_events(merged)
            # aircraft: the on-disk log (accumulates across fuses and restarts) plus the in-memory tail
            archive = _tracks_to_archive([tr for tr in track_history if inside(tr.lat, tr.lon)])
            span = None
            if self.aircraft_log is not None:
                self.progress = "loading aircraft history"
                logged, span = self.aircraft_log.load(t_min, t_max)
                for hexid, a in logged.items():
                    pts = [p for p in a["points"] if inside(p[1], p[2])]
                    if not pts:
                        continue
                    dst = archive.setdefault(hexid, {"hex": hexid, "military": False, "points": []})
                    dst["military"] = dst["military"] or a["military"]
                    dst["points"] = sorted(dst["points"] + pts, key=lambda p: p[0])
            b.add_tracks(archive)
            stamps_t = [datetime.fromisoformat(tr.ts).timestamp() for tr in track_history]
            bounds = ([span[0], span[1]] if span else []) + stamps_t
            if bounds:
                b.set_track_coverage(min(bounds), max(bounds))
            else:
                b.set_track_coverage(now + 1, now + 2)      # no aircraft history at all
            b.add_firms([h for h in firms if inside(h["lat"], h["lon"])])
            self.progress = "computing baseline and incidents"
            tracker = IncidentTracker(b, merged)
            self._assign_durable_ids(tracker.at(now), now)
            with self.lock:
                self.baseline, self.tracker, self.built_at = b, tracker, time.time()
                self.snapshot_taken = snapshot_taken
            self.progress = "done"
            log.info("live analysis: %d events, %d cells, %d incidents now, %.0fs",
                     len(merged), len(b.cells()), len(tracker.at(now)), time.time() - t0)
        except Exception as e:
            self.progress = f"failed: {str(e)[:120]}"
            log.exception("live analysis build failed")

    def snapshot(self) -> dict:
        with self.lock:
            b, tracker, built = self.baseline, self.tracker, self.built_at
        # assessments are as of the build, not the wall clock: the current hour was only partly
        # collected when the build ran, so the clock crossing an hour must not promote it to complete
        taken = getattr(self, "snapshot_taken", built)
        now = min(datetime.now(timezone.utc).timestamp(), taken) if taken else datetime.now(timezone.utc).timestamp()
        if not b or not tracker:
            return {"status": self.progress, "built_at": None, "incidents": [], "departures": [], "departed_cells": []}
        return {
            "status": self.progress,
            "built_at": datetime.fromtimestamp(built, tz=timezone.utc).isoformat() if built else None,
            "t": now,
            "window": {"t_min": b.t_min, "t_max": b.t_max, "hours": HOURS},
            "baseline": {"z_threshold": 2.0, "reference": "same hour +/-2 h on the prior day, 2-3 h away today",
                         "days": 2, "note": "aircraft history accumulates on disk from first run; bins before it read insufficient"},
            "aircraft_history": ({"from": datetime.fromtimestamp(b.track_coverage[0], tz=timezone.utc).isoformat(),
                                  "to": datetime.fromtimestamp(b.track_coverage[1], tz=timezone.utc).isoformat()}
                                 if b.track_coverage and b.track_coverage[0] < now else None),
            "assessed_through": (datetime.fromtimestamp(b.bin_end(b.completed_bin(now)), tz=timezone.utc).isoformat() if b.completed_bin(now) is not None else None),
            "incidents": [{**inc.to_dict(), "id": self._id_map.get(inc.id, inc.id)} for inc in tracker.at(now)],
            "departures": [d.to_dict() for d in b.departures_at(now)],
            "departed_cells": sorted([list(c) for c in b.departed_cells(now)]),
            "timeline_z": b.timeline(),
            "t_min": b.t_min, "step_s": b.step,
        }
