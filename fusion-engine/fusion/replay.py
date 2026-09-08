"""Replay mode: fuse historical GDELT + archived ADS-B at any instant of a chosen day.

Inputs (built once):
  data/replay/<day>_gdelt.json   from  python -m fusion.replay_gdelt <day>
  data/replay/<day>_adsb.json    from  python -m fusion.replay_adsb extract <archive_dir>
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from .ingest_gdelt import OsintEvent
from .neo4j_store import Neo4jStore
from .ingest_telegram import SocialPost, social_to_event
from .replay_adsb import load_tracks, snapshot_at, track_polylines
from .replay_gdelt import HORMUZ_BBOX, HORMUZ_KW, load_day

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

SCENARIOS = {
    "hormuz-2026-08-18": {
        "title": "Strait of Hormuz, 18 Aug 2026",
        "day": "2026-08-18",
        "bbox": HORMUZ_BBOX,
        "keywords": HORMUZ_KW,
        "center": (26.0, 55.5),
        "zoom": 7,
        "notes": "US-Iran ceasefire expiry; vessel struck by projectile exiting Hormuz; Iranian missile fire toward UAE.",
        "sources": [
            ("Vessel hit by projectile while exiting Strait of Hormuz", "https://shipandbunker.com/news/emea/178048-vessel-hit-by-projectile-while-exiting-strait-of-hormuz"),
            ("Ship attacked in Hormuz as US-Iran ceasefire expiry risks prolonged conflict", "https://www.cnbcafrica.com/2026/ship-attacked-in-hormuz-strait-as-u-s-iran-ceasefire-expiry-risks-prolonged-conflict"),
            ("Crew killed in vessel strike in Strait of Hormuz", "https://economictimes.indiatimes.com/news/international/world-news/us-iran-war-crew-killed-in-vessel-strike-in-strait-of"),
            ("Iran fires ballistic missiles at UAE", "https://www.jns.org/news/world/iran-fires-ballistic-missiles-at-uae"),
            ("UAE warns of potential missile threats", "https://keralakaumudi.com/en/en/world/gulf/uae-warns-of-potential-missile-threats-1793501"),
            ("Iran threatens escalation as Hormuz crisis deepens", "https://gulfnews.com/world/mena/middle-east-war-iran-threatens-escalation-as-hormuz-crisis-deepens-1.500644222"),
            ("CNN: Iran, the Strait of Hormuz and oil", "https://edition.cnn.com/2026/08/18/business/iran-strait-of-hormuz-oil"),
        ],
    },
}


class ReplayState:
    def __init__(self, scenario_id: str, store: Neo4jStore | None = None):
        self.sc = dict(SCENARIOS[scenario_id], id=scenario_id)
        self.day = self.sc["day"]
        self.events: list[OsintEvent] = []
        self.tracks: dict[str, dict] = {}
        self.loaded = False
        self.lock = threading.Lock()
        self._cache: dict[int, dict] = {}
        self.store = store or Neo4jStore()
        d = datetime.strptime(self.day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        self.t_min = d.timestamp()
        self.t_max = self.t_min + 86400 - 1

    def load(self):
        with self.lock:
            if self.loaded:
                return
            gpath = DATA / "replay" / f"{self.day}_gdelt.json"
            if gpath.exists():
                self.events = [OsintEvent(**e) for e in json.loads(gpath.read_text(encoding="utf-8"))]
            else:
                log.info("building GDELT replay for %s (first time, may take minutes)", self.day)
                self.events = load_day(self.day, DATA / "gdelt", self.sc["bbox"], self.sc["keywords"])
                gpath.parent.mkdir(parents=True, exist_ok=True)
                gpath.write_text(json.dumps([e.to_dict() for e in self.events]), encoding="utf-8")
            tpath = DATA / "replay" / f"{self.day}_telegram.json"
            if tpath.exists():
                posts = [SocialPost(**d) for d in json.loads(tpath.read_text(encoding="utf-8"))]
                social = [social_to_event(p) for p in posts if p.lat is not None]
                self.events += social
                log.info("replay %s: +%d geolocated Telegram posts (%d total posts)", self.day, len(social), len(posts))
            apath = DATA / "replay" / f"{self.day}_adsb.json"
            if apath.exists():
                self.tracks = load_tracks(apath)
            else:
                log.warning("no ADS-B replay file %s - run fusion.replay_adsb extract", apath)
            self.loaded = True
            log.info("replay %s: %d events, %d aircraft", self.day, len(self.events), len(self.tracks))

    def config(self) -> dict:
        self.load()
        return {
            "scenario": self.sc, "t_min": self.t_min, "t_max": self.t_max,
            "n_events": len(self.events), "n_conflict": sum(e.is_conflict for e in self.events),
            "n_aircraft": len(self.tracks), "n_military": sum(1 for a in self.tracks.values() if a["military"]),
            "adsb_available": bool(self.tracks),
        }

    def at(self, t: float, lookback_min: float = 120.0, radius_km: float = 75.0, tail_min: float = 30.0) -> dict:
        """Fused picture at instant t (epoch seconds). Events from the prior lookback window, aircraft
        positions as of t, correlations between them. Cached per minute."""
        self.load()
        t = min(max(t, self.t_min), self.t_max)
        key = int(t // 60)
        if key in self._cache:
            return self._cache[key]
        t_iso = datetime.fromtimestamp(t, tz=timezone.utc)
        ev = [e for e in self.events
              if t - lookback_min * 60 <= datetime.fromisoformat(e.ts).timestamp() <= t]
        tr = snapshot_at(self.tracks, t) if self.tracks else []
        batch_id = f"replay:{self.sc['id']}:{key}"
        event_ids = [event.id for event in ev]
        self.store.ingest(ev, tr, batch_id)
        alerts = self.store.correlate(event_ids, batch_id, radius_km, lookback_min, min_severity=0.3)
        stored_events = self.store.events(event_ids, limit=20000)
        stored_tracks = self.store.aircraft(batch_id)
        out = {
            "t": t, "t_iso": t_iso.isoformat(),
            "counts": {"events": len(stored_events), "conflict_events": sum(e["is_conflict"] for e in stored_events),
                       "tracks": len(stored_tracks), "military_tracks": sum(x["military"] for x in stored_tracks), "alerts": len(alerts)},
            "events": stored_events,
            "tracks": stored_tracks,
            "alerts": [a.to_dict() for a in alerts[:300]],
            "graph": self.store.graph(event_ids, batch_id, max_nodes=220, max_links=400),
            "tails": track_polylines(self.tracks, t - tail_min * 60, t) if self.tracks else [],
        }
        if len(self._cache) > 200:
            self._cache.clear()
        self._cache[key] = out
        return out

    def timeline(self, step_min: int = 15) -> list[dict]:
        """Event volume per step for the scrubber histogram."""
        self.load()
        bins: dict[int, dict] = {}
        for e in self.events:
            b = int((datetime.fromisoformat(e.ts).timestamp() - self.t_min) // (step_min * 60))
            d = bins.setdefault(b, {"t": self.t_min + b * step_min * 60, "events": 0, "conflict": 0})
            d["events"] += 1
            d["conflict"] += int(e.is_conflict)
        return [bins[k] for k in sorted(bins)]
