"""Streaming fusion pipeline: poll GDELT (15 min) + adsb.lol (60 s), correlate, persist snapshot.

Run once:      python -m fusion.pipeline
Run forever:   python -m fusion.pipeline --loop
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .store import make_store
from .ingest_adsb import AirTrack, fetch_military, fetch_regions
from .ingest_gdelt import OsintEvent, fetch_window
from .ingest_telegram import DEFAULT_CHANNELS, fetch_latest, social_to_event
from .ingest_firms import fetch as fetch_firms, novelty as firms_novelty
from .backfill import Backfill

log = logging.getLogger(__name__)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except Exception:
    pass
DATA = Path(os.getenv("FUSION_DATA_DIR") or (Path(__file__).resolve().parent.parent / "data"))   # keep this out of OneDrive-synced folders

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
    store: object = field(default_factory=make_store, repr=False)
    social: list[OsintEvent] = field(default_factory=list)
    firms: list[dict] = field(default_factory=list)
    # This is deliberately separate from record counts.  An empty result can be
    # valid (for example, no new thermal pixels), while a source can also be
    # waiting, unavailable, or missing configuration.
    source_status: dict[str, dict] = field(default_factory=lambda: {
        "gdelt": {"state": "starting", "label": "GDELT OSINT"},
        "adsb": {"state": "starting", "label": "ADS-B aircraft"},
        "firms": {"state": "starting", "label": "NASA FIRMS thermal"},
        "telegram": {"state": "starting", "label": "Telegram previews"},
        "fusion": {"state": "starting", "label": "Fusion correlations"},
    })
    history: list[dict] = field(default_factory=lambda: _load_history())   # per-fuse counts, persisted across restarts
    backfill: Backfill = field(default_factory=lambda: Backfill(DATA / "gdelt", hours=float(os.getenv("FUSION_BACKFILL_H", "48"))), repr=False)
    _seen: dict = field(default_factory=lambda: {"events": {}, "social": {}, "alerts": {}, "firms": {}, "tracks": {}}, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set_source_status(self, source: str, state: str, *, count: int | None = None, detail: str | None = None):
        """Publish source readiness without exposing secrets or raw provider errors."""
        with self.lock:
            current = self.source_status.get(source, {})
            self.source_status[source] = {
                **current,
                "state": state,
                "updated": datetime.now(timezone.utc).isoformat(),
                **({"count": count} if count is not None else {}),
                **({"detail": detail} if detail else {}),
            }

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

    def rename_region(self, rid: str, name: str) -> dict | None:
        with self.lock:
            for r in self.regions:
                if r["id"] == rid:
                    r["name"] = name.strip()[:60] or r["name"]
                    save_regions(self.regions)
                    return dict(r)
        return None

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
            all_events = self.events + self.social
            self.event_ids = [event.id for event in all_events]
            self.conflict_event_count = sum(event.is_conflict for event in all_events)
        log.info("GDELT: %d geocoded events (%d conflict) from %d window(s)",
                 len(all_ev), sum(e.is_conflict for e in all_ev), windows)
        self.set_source_status("gdelt", "ready", count=len(all_ev), detail=f"{windows} × 15-minute window")

    def refresh_social(self, channels=None):
        """Poll public Telegram channel previews; keep the last 6 h of geolocated posts."""
        posts = []
        failures = 0
        for ch in channels or DEFAULT_CHANNELS:
            try:
                posts += fetch_latest(ch)
            except Exception as e:
                log.warning("telegram %s failed: %s", ch, e)
                failures += 1
        cutoff = datetime.now(timezone.utc).timestamp() - 6 * 3600
        fresh = [social_to_event(p) for p in posts
                 if p.lat is not None and datetime.fromisoformat(p.ts).timestamp() >= cutoff]
        with self.lock:
            have = {e.id for e in self.social}
            self.social = [e for e in self.social
                           if datetime.fromisoformat(e.ts).timestamp() >= cutoff] + [e for e in fresh if e.id not in have]
            all_events = self.events + self.social
            self.event_ids = [event.id for event in all_events]
            self.conflict_event_count = sum(event.is_conflict for event in all_events)
        log.info("Telegram: %d posts polled, %d geolocated in last 6 h", len(posts), len(self.social))
        state = "error" if failures and not posts else "partial" if failures else "ready"
        detail = "Some channel previews were unavailable" if failures else "Public channel previews only"
        self.set_source_status("telegram", state, count=len(self.social), detail=detail)

    def refresh_firms(self):
        """Latest 24 h of VIIRS thermal anomalies inside every area-of-interest circle, scored for
        novelty against the previous two days (routine flares score ~0)."""
        from datetime import timedelta
        with self.lock:
            circles = list(self.regions)
        out = []
        seen = set()
        failures: list[Exception] = []
        # rolling baseline: the two days before today (flare pixels wander ~1-2 km between passes,
        # so a single day a week earlier over-flags routine flares as new)
        base_day = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%d")
        for r in circles:
            d = r.get("radius_nm", 250) * 1.852 / 111.0            # deg of latitude
            dlon = d / max(0.2, abs(__import__("math").cos(__import__("math").radians(r["lat"]))))
            bbox = (max(-90, r["lat"] - d), max(-180, r["lon"] - dlon), min(90, r["lat"] + d), min(180, r["lon"] + dlon))
            try:
                hs = fetch_firms(bbox, None, days=1)
                base = fetch_firms(bbox, base_day, days=2)
                hs = firms_novelty(hs, base)
            except Exception as e:
                log.warning("FIRMS %s failed: %s", r.get("name"), e)
                failures.append(e)
                continue
            for h in hs:
                if h.id not in seen:
                    seen.add(h.id)
                    out.append(h.to_dict())
        with self.lock:
            self.firms = out
        log.info("FIRMS live: %d hotspots in %d circles (%d novel)", len(out), len(circles),
                 sum(1 for h in out if h["novelty"] >= 0.9))
        if failures and not out:
            missing_key = any("FIRMS_MAP_KEY not set" in str(error) for error in failures)
            detail = "Set FIRMS_MAP_KEY in .env to enable this source" if missing_key else "Thermal provider unavailable; the map may show cached data"
            self.set_source_status("firms", "error", count=0, detail=detail)
        else:
            state = "partial" if failures else "ready"
            detail = "Some AOIs could not be queried" if failures else "Latest 24 hours; novel against a two-day baseline"
            self.set_source_status("firms", state, count=len(out), detail=detail)

    def refresh_adsb(self, regions=True):
        # The public military endpoint returns first.  Publish it immediately
        # instead of keeping the source in "starting" while six rate-limited
        # AOI point requests run one after another.
        tracks = fetch_military()
        with self.lock:
            self.tracks = list(tracks)
            self.military_track_count = sum(track.military for track in tracks)
        self.set_source_status("adsb", "partial", count=len(tracks), detail="Military feed live; collecting configured AOI traffic")
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
        self.set_source_status("adsb", "ready", count=len(tracks), detail="Military feed plus configured AOIs")

    def fuse(self, radius_km=75.0, window_min=240.0):
        with self.lock:
            ev, tr = list(self.events) + list(self.social), list(self.tracks)
            event_ids = [event.id for event in ev]
        batch_id = f"live:{uuid.uuid4().hex}"
        self.store.ingest(ev, tr, batch_id)
        alerts = self.store.correlate(event_ids, batch_id, radius_km, window_min, min_severity=0.35)
        with self.lock:
            self.event_ids = event_ids
            self.conflict_event_count = sum(event.is_conflict for event in ev)
            self.batch_id = batch_id
            self.updated = datetime.now(timezone.utc).isoformat()
            self.alert_count = len(alerts)
            now_ts = datetime.now(timezone.utc).timestamp()
            # levels (what is present now) and flows (first seen since the previous fuse) — the live
            # timeline plots flows for events/posts/alerts/anomalies, levels for aircraft
            primed = any(self._seen.values())            # False on the first fuse of this process
            def first_seen(kind, ids):
                seen = self._seen[kind]
                new = [i for i in ids if i not in seen]
                for i in ids:
                    seen[i] = now_ts
                if len(seen) > 300_000:                       # bound memory: forget ids older than 24 h
                    for k in [k for k, t in seen.items() if now_ts - t > 86400]:
                        seen.pop(k, None)
                return len(new)
            ev_new = first_seen("events", [e.id for e in self.events if e.is_conflict])
            so_new = first_seen("social", [e.id for e in self.social])
            al_new = first_seen("alerts", [a.id for a in alerts])
            fi_new = first_seen("firms", [h["id"] for h in self.firms if h.get("novelty", 0) >= 0.9])
            tr_new = first_seen("tracks", [t.hex for t in tr])
            point = {"t": now_ts, "events": len(self.events), "conflict": sum(e.is_conflict for e in self.events),
                     "social": len(self.social), "tracks": len(tr), "military": sum(t.military for t in tr),
                     "alerts": len(alerts), "firms_new": sum(1 for h in self.firms if h.get("novelty", 0) >= 0.9),
                     "d_conflict": ev_new, "d_social": so_new, "d_alerts": al_new, "d_firms_new": fi_new, "d_tracks": tr_new,
                     "primed": primed}
            self.history.append(point)
            self.history = [h for h in self.history if now_ts - h["t"] <= HISTORY_KEEP_H * 3600]
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(HISTORY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(point) + "\n")
        except Exception as e:
            log.warning("history append failed: %s", e)
        log.info("FUSE: persisted %d events / %d tracks, %d alerts (top=%s)",
                 len(ev), len(tr), len(alerts),
                 alerts[0].score if alerts else None)
        self.set_source_status("fusion", "ready", count=len(alerts), detail="Spatial and temporal correlations")
        return alerts

    def close(self):
        self.store.close()

    def api_events(self, conflict_only: bool = False, limit: int = 3000) -> list[dict]:
        with self.lock:
            event_ids = list(self.event_ids)
        return self.store.events(event_ids, conflict_only, limit)

    def api_aircraft(self, military_only: bool = False) -> list[dict]:
        """Return the latest ingest snapshot, including the early military result.

        The graph store is updated after all AOI point requests finish and fusion
        runs.  Serving it here made the map wait on those rate-limited requests
        even though ``refresh_adsb`` had already received the military feed.
        """
        with self.lock:
            tracks = [track.to_dict() for track in self.tracks]
        return [track for track in tracks if not military_only or track["military"]]

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
                "store": getattr(self.store, "name", "?"),
                "sources": {name: dict(value) for name, value in self.source_status.items()},
                "counts": {
                    "events": len(self.event_ids),
                    "conflict_events": self.conflict_event_count,
                    "tracks": len(self.tracks),
                    "military_tracks": self.military_track_count,
                    "alerts": self.alert_count,
                    "social": len(self.social),
                    "firms": len(self.firms),
                    "firms_novel": sum(1 for h in self.firms if h.get("novelty", 0) >= 0.9),
                },
            }

    def api_timeline(self, hours: float = 24.0) -> dict:
        """15-min bins for the last `hours`: GDELT/Telegram backfilled inside the drawn circles,
        FIRMS novel anomalies, plus our own aircraft levels and correlation flows."""
        hours = hours if hours > 0 else 24 * 7
        with self.lock:
            hist, firms = list(self.history), list(self.firms)
        bins = self.backfill.bins(hours, hist, firms)
        return {"step_min": 15, "hours": hours, "bins": bins,
                "backfill": {"status": self.backfill.progress, "hours": self.backfill.hours,
                             "windows": len(self.backfill.gdelt)},
                "since": min((h["t"] for h in hist), default=None)}

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


HISTORY_FILE = DATA / "history.jsonl"
HISTORY_KEEP_H = float(os.getenv("FUSION_HISTORY_H", "168"))     # keep 7 days on disk by default


def _load_history() -> list[dict]:
    """Reload the per-fuse activity points written by earlier runs (one JSON object per line)."""
    if not HISTORY_FILE.exists():
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - HISTORY_KEEP_H * 3600
    out = []
    for line in HISTORY_FILE.read_text(encoding="utf-8").splitlines():
        try:
            h = json.loads(line)
            if h.get("t", 0) >= cutoff:
                out.append(h)
        except ValueError:
            continue
    log.info("history: %d points reloaded from %s", len(out), HISTORY_FILE)
    return out


def _prev_window(stamp: str) -> str:
    from datetime import timedelta
    dt = datetime.strptime(stamp, "%Y%m%d%H%M%S") - timedelta(minutes=15)
    return dt.strftime("%Y%m%d%H%M%S")


def run_once(state: FusionState, windows=2) -> FusionState:
    state.refresh_gdelt(windows=windows)
    try:
        state.refresh_social()
    except Exception as e:
        log.warning("social refresh failed: %s", e)
    state.refresh_adsb()
    state.fuse()
    state.save()
    return state


def run_loop(state: FusionState, gdelt_every=900, adsb_every=60, windows=2, primed=True, social_every=300):
    """Background loop: ADS-B every minute, GDELT every 15 minutes, re-fuse after each.
    primed=True means run_once() already populated state, so the first tick waits."""
    last_g = time.time() if primed else 0.0
    last_s = last_g
    if primed:
        time.sleep(adsb_every)
    while True:
        now = time.time()
        if now - last_s >= social_every:
            try:
                state.refresh_social()
                last_s = now
            except Exception as e:
                log.warning("social refresh failed: %s", e)
                state.set_source_status("telegram", "error", detail="Telegram previews unavailable; retaining the last result")
        if now - last_g >= gdelt_every:
            try:
                state.refresh_gdelt(windows=windows)
                last_g = now
            except Exception as e:
                log.warning("GDELT refresh failed (keeping previous events): %s", e)
                state.set_source_status("gdelt", "error", detail="OSINT feed unavailable; retaining the last result")
            try:
                state.refresh_firms()
            except Exception as e:
                log.warning("FIRMS refresh failed: %s", e)
                state.set_source_status("firms", "error", detail="Thermal provider unavailable; the map may show cached data")
            try:
                state.store.prune(max_age_h=24.0)
            except Exception as e:
                log.warning("store prune failed: %s", e)
            try:
                with state.lock:
                    circles = list(state.regions)
                if state.backfill.built_at is None:
                    state.backfill.start(circles)
                else:
                    state.backfill.extend(circles)
            except Exception as e:
                log.warning("backfill failed: %s", e)
        try:
            state.refresh_adsb()
        except Exception as e:
            log.warning("ADS-B refresh failed (keeping previous tracks): %s", e)
            state.set_source_status("adsb", "error", detail="Aircraft feed unavailable; retaining the last result")
        try:
            state.fuse()
            state.save()
        except Exception as e:
            log.exception("fuse failed: %s", e)
            state.set_source_status("fusion", "error", detail="Correlation pass failed; retaining the last result")
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
        for a in st.api_alerts(limit=15):
            print(f"{a['score']:.3f}  {a['aircraft_label']:26s} {a['distance_km']:6.1f} km  {a['event_label'][:60]}")
