"""Streaming fusion pipeline: poll GDELT (15 min) + adsb.lol (60 s), correlate, persist snapshot.

Run once:      python -m fusion.pipeline
Run forever:   python -m fusion.pipeline --loop
"""
from __future__ import annotations

import argparse
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .neo4j_store import Neo4jStore
from .ingest_adsb import AirTrack, fetch_military, fetch_regions
from .ingest_gdelt import OsintEvent, fetch_window

log = logging.getLogger(__name__)
DATA = Path(__file__).resolve().parent.parent / "data"

# Areas of interest: circles (lat, lon, radius_nm) unioned with the global military feed for live
# ADS-B, and drawn on the map. adsb.lol caps point queries at 250 nm. Users can add/remove circles
# from the dashboard (Redfin-style draw); the list persists in data/regions.json.
DEFAULT_REGIONS = [
    {"id": "hormuz", "name": "Strait of Hormuz", "lat": 26.0, "lon": 55.5, "radius_nm": 250},
    {"id": "dc", "name": "Washington DC", "lat": 38.9, "lon": -77.0, "radius_nm": 250},
    {"id": "kyiv", "name": "Kyiv", "lat": 50.4, "lon": 30.5, "radius_nm": 250},
    {"id": "levant", "name": "Levant", "lat": 31.8, "lon": 35.2, "radius_nm": 250},
    {"id": "taiwan", "name": "Taiwan Strait", "lat": 25.0, "lon": 121.5, "radius_nm": 250},
    {"id": "baltic", "name": "Baltic / Kaliningrad", "lat": 54.7, "lon": 20.5, "radius_nm": 250},
]
REGIONS_FILE = DATA / "regions.json"


def load_regions() -> list[dict]:
    if REGIONS_FILE.exists():
        try:
            return json.loads(REGIONS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.warning("regions.json unreadable (%s), using defaults", e)
    return [dict(r) for r in DEFAULT_REGIONS]


def save_regions(regions: list[dict]):
    REGIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    REGIONS_FILE.write_text(json.dumps(regions, indent=1), encoding="utf-8")


@dataclass
class FusionState:
    gdelt_stamp: str | None = None
    events: list[OsintEvent] = field(default_factory=list)
    tracks: list[AirTrack] = field(default_factory=list)
    event_ids: list[str] = field(default_factory=list)
    batch_id: str | None = None
    updated: str | None = None
    conflict_event_count: int = 0
    military_track_count: int = 0
    alert_count: int = 0
    regions: list[dict] = field(default_factory=load_regions)
    store: Neo4jStore = field(default_factory=Neo4jStore, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # --- areas of interest ---
    def add_region(self, lat: float, lon: float, radius_nm: float, name: str | None = None) -> dict:
        import uuid
        r = {"id": uuid.uuid4().hex[:8], "name": name or f"AOI {lat:.2f},{lon:.2f}",
             "lat": round(lat, 4), "lon": round(lon, 4), "radius_nm": round(min(max(radius_nm, 5), 250), 1),
             "user": True}
        with self.lock:
            self.regions.append(r)
            save_regions(self.regions)
        return r

    def remove_region(self, rid: str) -> bool:
        with self.lock:
            n = len(self.regions)
            self.regions = [r for r in self.regions if r["id"] != rid]
            changed = len(self.regions) != n
            if changed:
                save_regions(self.regions)
        return changed

    # --- ingest steps ---
    def refresh_gdelt(self, windows: int = 1):
        """Pull the latest N 15-minute windows (N>1 walks backwards for a longer OSINT horizon)."""
        stamp, ev = fetch_window(cache_dir=DATA / "gdelt")
        all_ev = list(ev)
        s = stamp
        for _ in range(windows - 1):
            s = _prev_window(s)
            try:
                _, more = fetch_window(s, cache_dir=DATA / "gdelt")
                all_ev.extend(more)
            except Exception as e:
                log.warning("window %s failed: %s", s, e)
        with self.lock:
            self.gdelt_stamp = stamp
            self.events = all_ev
            self.event_ids = [event.id for event in all_ev]
            self.conflict_event_count = sum(event.is_conflict for event in all_ev)
        log.info("GDELT: %d geocoded events (%d conflict) from %d window(s)",
                 len(all_ev), sum(e.is_conflict for e in all_ev), windows)

    def refresh_adsb(self, regions=True):
        tracks = fetch_military()
        if regions:
            with self.lock:
                circles = [(r["lat"], r["lon"], r.get("radius_nm", 250)) for r in self.regions]
            civ = fetch_regions(circles)
            have = {t.hex for t in tracks}
            tracks += [t for t in civ if t.hex not in have]
        with self.lock:
            self.tracks = tracks
            self.military_track_count = sum(track.military for track in tracks)
        log.info("ADS-B: %d tracks (%d military)", len(tracks), sum(t.military for t in tracks))

    def fuse(self, radius_km=75.0, window_min=240.0):
        with self.lock:
            ev, tr = list(self.events), list(self.tracks)
            event_ids = list(self.event_ids)
        batch_id = f"live:{uuid.uuid4().hex}"
        self.store.ingest(ev, tr, batch_id)
        alerts = self.store.correlate(event_ids, batch_id, radius_km, window_min, min_severity=0.35)
        with self.lock:
            self.batch_id = batch_id
            self.updated = datetime.now(timezone.utc).isoformat()
            self.alert_count = len(alerts)
        log.info("FUSE: persisted %d events / %d tracks, %d alerts (top=%s)",
                 len(ev), len(tr), len(alerts),
                 alerts[0].score if alerts else None)
        return alerts

    def close(self):
        self.store.close()

    def api_events(self, conflict_only: bool = False, limit: int = 3000) -> list[dict]:
        with self.lock:
            event_ids = list(self.event_ids)
        return self.store.events(event_ids, conflict_only, limit)

    def api_aircraft(self, military_only: bool = False) -> list[dict]:
        with self.lock:
            batch_id = self.batch_id
        return self.store.aircraft(batch_id, military_only)

    def api_alerts(self, min_score: float = 0.0, limit: int = 100) -> list[dict]:
        with self.lock:
            event_ids, batch_id = list(self.event_ids), self.batch_id
        if not batch_id:
            return []
        return [alert.to_dict() for alert in self.store.alerts(event_ids, batch_id, min_score, limit)]

    def api_graph(self, max_nodes: int = 220, max_links: int = 400) -> dict:
        with self.lock:
            event_ids, batch_id = list(self.event_ids), self.batch_id
        return self.store.graph(event_ids, batch_id, max_nodes=max_nodes, max_links=max_links)

    def api_status(self) -> dict:
        """Fast in-memory status for the live refresh timer.

        The detailed dashboard endpoints query Neo4j independently.  Status must
        stay cheap so it cannot hold up those requests once history has grown.
        """
        with self.lock:
            return {
                "updated": self.updated,
                "gdelt_window": self.gdelt_stamp,
                "counts": {
                    "events": len(self.event_ids),
                    "conflict_events": self.conflict_event_count,
                    "tracks": len(self.tracks),
                    "military_tracks": self.military_track_count,
                    "alerts": self.alert_count,
                },
            }

    def api_entity(self, node_id: str) -> dict | None:
        return self.store.entity(node_id)

    # --- persistence ---
    def snapshot(self, include_graph: bool = True) -> dict:
        with self.lock:
            event_ids, batch_id = list(self.event_ids), self.batch_id
            updated, gdelt_stamp, regions = self.updated, self.gdelt_stamp, list(self.regions)
        events = self.store.events(event_ids, limit=20000)
        tracks = self.store.aircraft(batch_id)
        alerts = self.store.alerts(event_ids, batch_id, limit=2000) if batch_id else []
        return {
            "updated": updated,
            "gdelt_window": gdelt_stamp,
            "regions": regions,
            "counts": {
                "events": len(event_ids),
                "conflict_events": sum(event["is_conflict"] for event in events),
                "tracks": len(tracks),
                "military_tracks": sum(track["military"] for track in tracks),
                "alerts": len(alerts),
            },
            "events": events,
            "tracks": tracks,
            "alerts": [alert.to_dict() for alert in alerts],
            "graph": self.store.graph(event_ids, batch_id) if include_graph else {"nodes": [], "links": [], "stats": {}},
        }

    def save(self, path: Path = DATA / "snapshot.json"):
        path.parent.mkdir(parents=True, exist_ok=True)
        # This periodic artifact is for recovery/audit.  Building a browser graph
        # projection on every 60-second ingest needlessly competes with the UI.
        path.write_text(json.dumps(self.snapshot(include_graph=False)), encoding="utf-8")
        log.info("saved %s", path)


def _prev_window(stamp: str) -> str:
    from datetime import timedelta
    dt = datetime.strptime(stamp, "%Y%m%d%H%M%S") - timedelta(minutes=15)
    return dt.strftime("%Y%m%d%H%M%S")


def run_once(state: FusionState, windows=2) -> FusionState:
    state.refresh_gdelt(windows=windows)
    state.refresh_adsb()
    state.fuse()
    state.save()
    return state


def run_loop(state: FusionState, gdelt_every=900, adsb_every=60, windows=2, primed=True):
    """Background loop: ADS-B every minute, GDELT every 15 minutes, re-fuse after each.
    primed=True means run_once() already populated state, so the first tick waits."""
    last_g = time.time() if primed else 0.0
    if primed:
        time.sleep(adsb_every)
    while True:
        now = time.time()
        if now - last_g >= gdelt_every:
            try:
                state.refresh_gdelt(windows=windows)
                last_g = now
            except Exception as e:
                log.warning("GDELT refresh failed (keeping previous events): %s", e)
        try:
            state.refresh_adsb()
        except Exception as e:
            log.warning("ADS-B refresh failed (keeping previous tracks): %s", e)
        try:
            state.fuse()
            state.save()
        except Exception as e:
            log.exception("fuse failed: %s", e)
        time.sleep(adsb_every)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--windows", type=int, default=2, help="GDELT 15-min windows to ingest")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    st = FusionState()
    if args.loop:
        run_loop(st, windows=args.windows)
    else:
        run_once(st, windows=args.windows)
        for a in st.alerts[:15]:
            print(f"{a.score:.3f}  {a.aircraft_label:26s} {a.distance_km:6.1f} km  {a.event_label[:60]}")
