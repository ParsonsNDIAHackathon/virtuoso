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

from .store import InMemoryStore, make_store
from .ingest_adsb import AirTrack, fetch_military, fetch_regions
from .ingest_gdelt import OsintEvent, fetch_window
from .ingest_social import PLATFORM_LABELS, SocialPost, enabled_platforms, platform_of, social_to_event
from .ingest_firms import fetch as fetch_firms, novelty as firms_novelty
from .backfill import Backfill
from .live_analysis import LiveAnalysis
from .aircraft_log import AircraftLog
from .fusion_ai import Assessment, Candidate, EvidenceRecord, FusionAI, FusionCluster, asserted_graph_context, candidate_for_pair, generate_candidates, records_from_sources

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
    track_history: list[AirTrack] = field(default_factory=list, repr=False)
    event_ids: list[str] = field(default_factory=list)
    batch_id: str | None = None
    updated: str | None = None
    conflict_event_count: int = 0
    military_track_count: int = 0
    alert_count: int = 0
    regions: list[dict] = field(default_factory=load_regions)
    store: object = field(default_factory=make_store, repr=False)
    social: list[OsintEvent] = field(default_factory=list)
    social_posts: dict[str, SocialPost] = field(default_factory=dict)
    firms: list[dict] = field(default_factory=list)
    fusion_ai: FusionAI = field(default_factory=lambda: FusionAI(DATA), repr=False)
    live_analysis: LiveAnalysis = field(default_factory=lambda: LiveAnalysis(DATA / "gdelt", aircraft_log=AircraftLog(DATA / "aircraft_log.jsonl"), state_path=DATA / "incidents_state.json"), repr=False)
    fusion_candidates: list[Candidate] = field(default_factory=list, repr=False)
    fusion_candidates_ungated_n: int = 0
    fusion_assessments: dict[str, Assessment] = field(default_factory=dict, repr=False)
    fusion_clusters: list[FusionCluster] = field(default_factory=list, repr=False)
    # This is deliberately separate from record counts.  An empty result can be
    # valid (for example, no new thermal pixels), while a source can also be
    # waiting, unavailable, or missing configuration.
    source_status: dict[str, dict] = field(default_factory=lambda: {
        "gdelt": {"state": "starting", "label": "GDELT OSINT"},
        "adsb": {"state": "starting", "label": "ADS-B aircraft"},
        "firms": {"state": "starting", "label": "NASA FIRMS thermal"},
        "social": {"state": "starting", "label": "Social (all platforms)"},
        "telegram": {"state": "starting", "label": "Telegram previews"},
        "fusion": {"state": "starting", "label": "Fusion candidate retrieval"},
        "openai": {"state": "starting", "label": "OpenAI adjudication"},
    })
    history: list[dict] = field(default_factory=lambda: _load_history())   # per-fuse counts, persisted across restarts
    backfill: Backfill = field(default_factory=lambda: Backfill(DATA / "gdelt", hours=float(os.getenv("FUSION_BACKFILL_H", "48"))), repr=False)
    _seen: dict = field(default_factory=lambda: {"events": {}, "social": {}, "alerts": {}, "firms": {}, "tracks": {}}, repr=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _prune_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _ai_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _fusion_persist_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _last_ai_at: float = field(default=0.0, repr=False)

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

    def refresh_social(self, channels=None, platforms=None, targets=None):
        """Poll every enabled social platform; keep the last 6 h of geolocated posts.

        `channels` is the legacy Telegram-only override (list of channel names).
        `platforms` / `targets` select a subset, e.g. platforms=["reddit"],
        targets={"reddit": ["worldnews"]}. Failures are isolated per platform so
        one down website never blocks the others.
        """
        import importlib
        from .ingest_social import _ADAPTERS, default_targets

        plats = enabled_platforms(platforms)
        if channels:  # legacy Telegram-only call path
            targets = {**(targets or {}), "telegram": list(channels)}
            plats = ["telegram"] if platforms is None else plats
        eff_targets = {p: (targets or {}).get(p) or default_targets(p) for p in plats}
        # Fan out per platform/target with explicit failure accounting: a
        # platform returning zero posts is healthy (nothing geolocated this
        # tick); only exceptions count as failures.
        posts, failures = [], 0
        per_platform: dict[str, int] = {}
        for plat in plats:
            try:
                mod = importlib.import_module(_ADAPTERS[plat])
            except Exception as e:
                log.warning("social %s unavailable: %s", plat, e)
                failures += 1
                continue
            for t in eff_targets.get(plat) or []:
                try:
                    chunk = mod.fetch_latest(t)
                except Exception as e:
                    log.warning("social %s %s failed: %s", plat, t, e)
                    failures += 1
                    continue
                for p in chunk:
                    per_platform[p.platform] = per_platform.get(p.platform, 0) + 1
                posts += chunk
        posts.sort(key=lambda p: p.ts or "")
        cutoff = datetime.now(timezone.utc).timestamp() - 6 * 3600
        fresh = [social_to_event(p) for p in posts
                 if p.lat is not None and datetime.fromisoformat(p.ts).timestamp() >= cutoff]
        with self.lock:
            have = {e.id for e in self.social}
            self.social = [e for e in self.social
                           if datetime.fromisoformat(e.ts).timestamp() >= cutoff] + [e for e in fresh if e.id not in have]
            self.social_posts = {
                **{post_id: post for post_id, post in getattr(self, "social_posts", {}).items()
                   if datetime.fromisoformat(post.ts).timestamp() >= cutoff},
                **{post.id: post for post in posts if post.lat is not None
                   and datetime.fromisoformat(post.ts).timestamp() >= cutoff},
            }
            all_events = self.events + self.social
            self.event_ids = [event.id for event in all_events]
            self.conflict_event_count = sum(event.is_conflict for event in all_events)
        # Per-platform geolocated counts (what actually enters the correlator).
        by_plat: dict[str, int] = {}
        for e in self.social:
            plat = platform_of(e) or "unknown"
            by_plat[plat] = by_plat.get(plat, 0) + 1
        log.info("Social: %d posts polled (%s), %d geolocated in last 6 h",
                 len(posts), ", ".join(f"{k}={v}" for k, v in sorted(per_platform.items())) or "none",
                 len(self.social))
        # Aggregate status (new) + legacy "telegram" key + per-platform keys.
        if failures and not posts:
            state, detail = "error", "All social sources unavailable; retaining the last result"
        elif failures:
            state, detail = "partial", "Some social sources were unavailable"
        else:
            state, detail = "ready", "Keyless public posts; geolocated only"
        self.set_source_status("social", state, count=len(self.social), detail=detail)
        tg_n = by_plat.get("telegram", 0)
        self.set_source_status("telegram",  # backwards-compat alias for old dashboards
                               state if "telegram" in plats else self.source_status.get("telegram", {}).get("state", "starting"),
                               count=tg_n if "telegram" in plats else self.source_status.get("telegram", {}).get("count"),
                               detail="Public channel previews only" if "telegram" in plats else None)
        for plat in plats:
            label = PLATFORM_LABELS.get(plat, plat)
            n = by_plat.get(plat, 0)
            # A platform that returned nothing this tick keeps its previous count
            # unless it errored on every target; fetch_latest_all already logged.
            self.set_source_status(f"social:{plat}", state, count=n, detail=label)

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
            hotspots, social_posts = list(self.firms), dict(self.social_posts)
            event_ids = [event.id for event in ev]
            cutoff = datetime.now(timezone.utc).timestamp() - 120 * 60
            self.track_history = [track for track in self.track_history if datetime.fromisoformat(track.ts).timestamp() >= cutoff]
            self.track_history.extend(tr)
        try:
            self.live_analysis.aircraft_log.record(tr)
        except Exception as e:
            log.warning("aircraft log write failed: %s", e)
        batch_id = f"live:{uuid.uuid4().hex}"
        store = self.store
        degraded_detail = None

        def persist_and_correlate():
            self.set_source_status("fusion", "starting", detail="Writing observations to the graph")
            store.ingest(ev, tr, batch_id, hotspots=hotspots, social_posts=social_posts)
            self.set_source_status("fusion", "starting", detail="Computing spatial and temporal correlations")
            return store.correlate(event_ids, batch_id, radius_km, window_min, min_severity=0.35)

        # A graph operation must not be able to freeze the only live-refresh
        # worker indefinitely.  This has happened while Neo4j is available for
        # the initial health probe but is still unavailable for a write.  Keep
        # the graph path as the normal source of truth, but continue the live
        # dashboard with its equivalent in-memory engine if that happens.
        if getattr(store, "name", None) == "neo4j":
            completed = threading.Event()
            result: list[list] = []
            failure: list[BaseException] = []

            def graph_work():
                try:
                    result.append(persist_and_correlate())
                except BaseException as exc:
                    failure.append(exc)
                finally:
                    completed.set()

            threading.Thread(target=graph_work, name="fusion-neo4j-pass", daemon=True).start()
            timeout_s = float(os.getenv("FUSION_NEO4J_PASS_TIMEOUT_S", "20"))
            if not completed.wait(timeout_s):
                failure.append(TimeoutError(f"Neo4j graph pass exceeded {timeout_s:g} seconds"))
            if failure:
                error = failure[0]
                log.exception("Neo4j fusion pass failed; using the in-memory engine", exc_info=error)
                fallback = InMemoryStore()
                fallback.ingest(ev, tr, batch_id, hotspots=hotspots, social_posts=social_posts)
                alerts = fallback.correlate(event_ids, batch_id, radius_km, window_min, min_severity=0.35)
                with self.lock:
                    if self.store is store:
                        self.store = fallback
                degraded_detail = "Neo4j was unresponsive; correlations are running in memory"
            else:
                alerts = result[0]
        else:
            alerts = persist_and_correlate()
        with self.lock:
            self.event_ids = event_ids
            self.conflict_event_count = sum(event.is_conflict for event in ev)
            self.batch_id = batch_id
            self.updated = datetime.now(timezone.utc).isoformat()
            self.alert_count = len(alerts)
            # The deterministic pass deliberately creates candidates only. OpenAI adjudication
            # runs outside this one-minute ingest path and promotes supported evidence separately.
            records = records_from_sources(ev, tr, hotspots, social_posts)
            limit = int(os.getenv("FUSION_CANDIDATE_LIMIT", "250"))
            baseline = getattr(self.live_analysis, "baseline", None)
            if baseline is not None:
                from .baseline import cell_of
                from .mission import is_dateline
                departed = baseline.departed_cells(now_ts_gate := datetime.now(timezone.utc).timestamp())
                hot = {(c[0] + dy, c[1] + dx) for c in departed for dy in (-1, 0, 1) for dx in (-1, 0, 1)}
                gated = [r for r in records if cell_of(r.lat, r.lon) in hot
                         and not (r.kind == "gdelt" and is_dateline(r.label.split(": ", 1)[-1]))]
                self.fusion_candidates = generate_candidates(gated, limit=limit)
                self.fusion_candidates_ungated_n = len(generate_candidates(records, limit=limit))
            else:
                self.fusion_candidates = generate_candidates(records, limit=limit)
                self.fusion_candidates_ungated_n = len(self.fusion_candidates)
            current_candidate_ids = {candidate.id for candidate in self.fusion_candidates}
            self.fusion_assessments = {
                key: assessment for key, assessment in self.fusion_assessments.items()
                if assessment.candidate_id in current_candidate_ids
            }
            # Re-read the cache for every current pair. The cache key includes the records' claim
            # content, so a pair whose article was edited or withdrawn misses and its old verdict is
            # dropped immediately (outdated), rather than lingering until the next model pass.
            refreshed: dict[str, Assessment] = {}
            for candidate in self.fusion_candidates:
                try:
                    cached = self.fusion_ai.cached_assessment(candidate)
                except Exception:
                    cached = None
                if cached is not None:
                    refreshed[cached.id] = cached
            self.fusion_assessments = refreshed
            self.fusion_clusters = self.fusion_ai.clusters(self.fusion_assessments.values())
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
        self.set_source_status("fusion", "partial" if degraded_detail else "ready", count=len(alerts),
                               detail=degraded_detail or "Spatial and temporal candidate generation")
        self._persist_fusion_artifacts(
            self.store, self.fusion_candidates, list(self.fusion_assessments.values()),
            self.fusion_clusters, batch_id, "candidate",
        )
        self._start_ai_fusion(batch_id)
        # baseline + incidents over the last 48 h, rebuilt in the background every 15 min
        try:
            with self.lock:
                args = (list(self.events), list(self.social), list(self.firms), list(self.track_history), list(self.regions))
            self.live_analysis.start(*args)
        except Exception as e:
            log.warning("live analysis not started: %s", e)
        return alerts

    def _persist_fusion_artifacts(self, store, candidates, assessments, clusters,
                                  batch_id: str, source: str) -> bool:
        """Serialize graph artifact writes and keep provider results independent of graph health."""
        try:
            with self._fusion_persist_lock:
                store.record_fusion(candidates, assessments, clusters, batch_id)
            return True
        except Exception as error:
            log.warning("%s fusion persistence failed; result retained in memory: %s",
                        source, str(error)[:300])
            return False

    def _start_ai_fusion(self, batch_id: str):
        """Adjudicate the best current candidates without delaying source ingestion."""
        if not self.fusion_ai.available:
            self.set_source_status("openai", "error", count=0,
                                   detail="Set OPENAI_API_KEY to enable evidence adjudication")
            return
        now = time.time()
        cadence = float(os.getenv("FUSION_LLM_EVERY_S", "600"))
        if now - self._last_ai_at < cadence or not self._ai_lock.acquire(blocking=False):
            return
        self._last_ai_at = now
        with self.lock:
            # priority: pairs inside current incident cells, then pairs with shared entities, then
            # news-to-news pairs (the ones most often supported), then retrieval score
            try:
                hot = {tuple(c) for inc in self.live_analysis.snapshot().get("incidents", []) for c in inc["cells"]}
            except Exception:
                hot = set()
            from .baseline import cell_of
            def _prio(c):
                inside = cell_of(c.left.lat, c.left.lon) in hot or cell_of(c.right.lat, c.right.lon) in hot
                return (not inside, -len(c.entity_overlap), not (c.left.kind == "gdelt" and c.right.kind == "gdelt"), -c.candidate_score)
            candidates = sorted(self.fusion_candidates, key=_prio)[:int(os.getenv("FUSION_LLM_MAX_CANDIDATES", "24"))]
            store = self.store
        if not candidates:
            self._ai_lock.release()
            self.set_source_status("openai", "ready", count=0, detail="No candidates require adjudication")
            return

        def work():
            try:
                self.set_source_status("openai", "starting", detail=f"Adjudicating {len(candidates)} candidate pairs")
                completed: list[Assessment] = []
                for candidate in candidates:
                    completed.append(self.fusion_ai.adjudicate(candidate))
                with self.lock:
                    # the batch may have moved on during a slow pass; keep verdicts for pairs that still exist
                    current = {c.id for c in self.fusion_candidates}
                    for assessment in completed:
                        if assessment.candidate_id in current:
                            self.fusion_assessments[assessment.id] = assessment
                    clusters = self.fusion_ai.clusters(self.fusion_assessments.values())
                    all_assessments = list(self.fusion_assessments.values())
                if clusters:
                    try:
                        clusters[0] = self.fusion_ai.brief(clusters[0], all_assessments)
                    except Exception as error:
                        log.warning("cluster brief failed; retaining pair assessments: %s", error)
                with self.lock:
                    self.fusion_clusters = clusters
                self._persist_fusion_artifacts(
                    store, self.fusion_candidates, all_assessments, clusters,
                    batch_id, "automatic OpenAI",
                )
                self.set_source_status("openai", "ready", count=len(completed),
                                       detail="Evidence adjudication complete; plausible links need review")
            except Exception as e:
                log.exception("OpenAI fusion pass failed: %s", e)
                self.set_source_status("openai", "error", detail="OpenAI adjudication failed; candidates remain unpromoted")
            finally:
                self._ai_lock.release()

        threading.Thread(target=work, name="fusion-openai-pass", daemon=True).start()

    def close(self):
        self.store.close()

    def prune_async(self, max_age_h: float = 24.0):
        """Run graph retention outside the sole live-refresh worker.

        Neo4j pruning can wait on a large delete transaction.  It is routine
        housekeeping, never a prerequisite for the current fusion picture.
        """
        if not self._prune_lock.acquire(blocking=False):
            return
        store = self.store

        def work():
            try:
                store.prune(max_age_h=max_age_h)
            except Exception as e:
                log.warning("store prune failed: %s", e)
            finally:
                self._prune_lock.release()

        threading.Thread(target=work, name="fusion-store-prune", daemon=True).start()

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

    def api_incidents(self) -> dict:
        return self.live_analysis.snapshot()

    def api_navint(self) -> list[dict]:
        """Per-cell navigation-integrity picture from the current snapshot, with the count it rests on."""
        from .navint import cells_from_snapshot
        with self.lock:
            tracks = list(self.tracks)
        return cells_from_snapshot(tracks)

    def api_tails(self, minutes: float = 30.0) -> list[dict]:
        """Recent paths for aircraft in the current snapshot, from rolling ADS-B pulls."""
        with self.lock:
            current_ids = {track.id for track in self.tracks}
            cutoff = datetime.now(timezone.utc).timestamp() - minutes * 60
            history = [track for track in self.track_history
                       if track.id in current_ids and datetime.fromisoformat(track.ts).timestamp() >= cutoff]
        points: dict[str, list[AirTrack]] = {track_id: [] for track_id in current_ids}
        for track in history:
            points[track.id].append(track)
        out = []
        for track_id, records in points.items():
            # The military and AOI feeds can both report one aircraft in a cycle.
            # Keep one point per timestamp so paths have no zero-length segments.
            unique = {track.ts: track for track in records}
            ordered = [unique[ts] for ts in sorted(unique)]
            if len(ordered) >= 2:
                last = ordered[-1]
                out.append({"id": track_id, "hex": last.hex, "callsign": last.callsign,
                            "military": last.military, "coords": [[track.lat, track.lon] for track in ordered]})
        return out

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
                    "candidates": len(self.fusion_candidates),
                    "assessments": len(self.fusion_assessments),
                    "supported": sum(a.verdict == "SUPPORTED" for a in self.fusion_assessments.values()),
                    "plausible": sum(a.verdict == "PLAUSIBLE" for a in self.fusion_assessments.values()),
                    "clusters": len(self.fusion_clusters),
                },
            }

    def api_fusion_candidates(self, limit: int = 300) -> list[dict]:
        with self.lock:
            return [candidate.to_dict(False) for candidate in self.fusion_candidates[:limit]]

    def api_fusion_assessments(self, include_rejected: bool = False, limit: int = 300) -> list[dict]:
        with self.lock:
            values = sorted(self.fusion_assessments.values(), key=lambda value: value.evidence_strength, reverse=True)
        if not include_rejected:
            values = [value for value in values if value.verdict in ("SUPPORTED", "PLAUSIBLE") or value.has_article_match]
        return [value.to_dict() for value in values[:limit]]

    def api_fusion_clusters(self, limit: int = 100) -> list[dict]:
        with self.lock:
            return [cluster.to_dict() for cluster in self.fusion_clusters[:limit]]

    def evidence_record(self, kind: str, record_id: str) -> EvidenceRecord | None:
        with self.lock:
            records = records_from_sources(list(self.events) + list(self.social), list(self.tracks),
                                           list(self.firms), dict(self.social_posts))
        record = next((value for value in records if value.kind == kind and value.id == record_id), None)
        if not record:
            return None
        # Only asserted source relationships enter the prompt. Proximity candidates and prior
        # model conclusions are excluded so the adjudicator cannot cite its own output.
        try:
            entity = self.store.entity(record_id)
        except Exception:
            entity = None
        record.graph_context = asserted_graph_context(entity, record_id)
        return record

    def adjudicate_pair(self, left_kind: str, left_id: str, right_kind: str, right_id: str, *, force: bool = False) -> dict:
        left, right = self.evidence_record(left_kind, left_id), self.evidence_record(right_kind, right_id)
        if not left or not right:
            raise KeyError("one or both evidence records are not in the current picture")
        candidate = candidate_for_pair(left, right)
        assessment = self.fusion_ai.adjudicate(candidate, force=force)
        with self.lock:
            if not any(value.id == candidate.id for value in self.fusion_candidates):
                self.fusion_candidates.append(candidate)
            self.fusion_assessments[assessment.id] = assessment
            self.fusion_clusters = self.fusion_ai.clusters(self.fusion_assessments.values())
            candidates, assessments, clusters, batch_id, store = (
                list(self.fusion_candidates), list(self.fusion_assessments.values()),
                list(self.fusion_clusters), self.batch_id, self.store,
            )
        if batch_id:
            args = (store, candidates, assessments, clusters, batch_id, "analyst OpenAI")
            if getattr(store, "name", None) == "neo4j":
                # The verdict is the requested operation. Graph persistence can overlap a live
                # ingest pass, so it must not hold the HTTP response open or relabel a database
                # deadlock as an OpenAI failure.
                threading.Thread(
                    target=self._persist_fusion_artifacts, args=args,
                    name="fusion-analyst-persist", daemon=True,
                ).start()
            else:
                self._persist_fusion_artifacts(*args)
        return assessment.to_dict()

    def api_timeline(self, hours: float = 24.0) -> dict:
        """15-min bins for the last `hours`: GDELT/social backfilled inside the drawn circles,
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
        # Always start the cycle with ADS-B.  GDELT downloads, FIRMS AOI calls,
        # and timeline backfill may be slow, but should never delay the first
        # usable air picture after a container restart.
        try:
            state.refresh_adsb()
        except Exception as e:
            log.warning("ADS-B refresh failed (keeping previous tracks): %s", e)
            state.set_source_status("adsb", "error", detail="Aircraft feed unavailable; retaining the last result")
        if now - last_s >= social_every:
            try:
                state.refresh_social()
                last_s = now
            except Exception as e:
                log.warning("social refresh failed: %s", e)
                state.set_source_status("social", "error", detail="Social sources unavailable; retaining the last result")
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
            state.prune_async(max_age_h=24.0)
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
