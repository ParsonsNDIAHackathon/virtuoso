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
from .store import make_store
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


def _nearest_scene(dets: list[dict], t: float, max_age_h: float = 72.0) -> tuple[list[dict], dict | None]:
    """Radar revisit over the strait is days, not hours: return the scene closest in time to t
    (before or after) within max_age_h, plus a descriptor with its age so the UI can say so."""
    times = sorted({datetime.fromisoformat(d["ts"]).timestamp() for d in dets})
    if not times:
        return [], None
    best = min(times, key=lambda x: abs(x - t))
    if abs(best - t) > max_age_h * 3600:
        return [], None
    scene = [d for d in dets if datetime.fromisoformat(d["ts"]).timestamp() == best]
    age_h = (t - best) / 3600
    return scene, {"ts": datetime.fromtimestamp(best, tz=timezone.utc).isoformat(), "age_h": round(age_h, 1),
                   "label": f"radar picture {abs(age_h):.0f} h {'before' if age_h > 0 else 'after'} this moment",
                   "n": len(scene), "scene": scene[0]["scene"] if scene else None}


class ReplayState:
    def __init__(self, scenario_id: str, store=None):
        self.sc = dict(SCENARIOS[scenario_id], id=scenario_id)
        self.day = self.sc["day"]
        self.events: list[OsintEvent] = []
        self.tracks: dict[str, dict] = {}
        self.firms: list[dict] = []
        self.sar: list[dict] = []
        self.loaded = False
        self.lock = threading.Lock()
        self._cache: dict[int, dict] = {}
        self.store = store or make_store()
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
            fpath = DATA / "replay" / f"{self.day}_firms.json"
            if fpath.exists():
                self.firms = json.loads(fpath.read_text(encoding="utf-8"))
            self.sar = []
            for spath in sorted((DATA / "replay").glob("*_sar.json")):
                try:
                    sday = datetime.strptime(spath.name[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
                except ValueError:
                    continue
                if abs(sday - self.t_min) <= 3 * 86400:
                    self.sar += json.loads(spath.read_text(encoding="utf-8"))
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
            "adsb_available": bool(self.tracks), "n_firms": len(self.firms), "n_sar": len(self.sar),
            "sar_scenes": sorted({d["ts"] for d in self.sar}),
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
            # thermal anomalies seen in the last 12 h (satellite passes are ~2x/day)
            "firms": [h for h in self.firms if t - 12 * 3600 <= datetime.fromisoformat(h["ts"]).timestamp() <= t],
            # radar ship detections from the most recent scene at or before t (within 12 h)
            "sar": (sar := _nearest_scene(self.sar, t))[0],
            "sar_scene": sar[1],
        }
        if len(self._cache) > 200:
            self._cache.clear()
        self._cache[key] = out
        return out

    def timeline(self, step_min: int = 15) -> dict:
        """Per-bin activity across every source for the scrubber strip:
        events (all / conflict), Telegram posts, aircraft and military aircraft with a position in the
        bin, new thermal anomalies (novelty >= 0.9), plus radar scene times as markers."""
        self.load()
        step = step_min * 60
        n = int(86400 // step)
        bins = [{"t": self.t_min + i * step, "events": 0, "conflict": 0, "social": 0,
                 "tracks": 0, "military": 0, "firms_new": 0} for i in range(n)]

        def idx(ts):
            i = int((ts - self.t_min) // step)
            return i if 0 <= i < n else None

        for e in self.events:
            i = idx(datetime.fromisoformat(e.ts).timestamp())
            if i is None:
                continue
            if e.source_domain.startswith("t.me/"):
                bins[i]["social"] += 1
            else:
                bins[i]["events"] += 1
                bins[i]["conflict"] += int(e.is_conflict)
        seen = [set() for _ in range(n)]
        mil = [set() for _ in range(n)]
        for hexid, a in self.tracks.items():
            for p in a["points"]:
                i = idx(p[0])
                if i is not None:
                    seen[i].add(hexid)
                    if a.get("military"):
                        mil[i].add(hexid)
        for i in range(n):
            bins[i]["tracks"] = len(seen[i])
            bins[i]["military"] = len(mil[i])
        for h in self.firms:
            i = idx(datetime.fromisoformat(h["ts"]).timestamp())
            if i is not None and h.get("novelty", 0) >= 0.9:
                bins[i]["firms_new"] += 1
        scenes = sorted({d["ts"] for d in self.sar})
        return {"step_min": step_min, "t_min": self.t_min, "bins": bins,
                "sar_scenes": [{"ts": ts, "t": datetime.fromisoformat(ts).timestamp(),
                                "n": sum(1 for d in self.sar if d["ts"] == ts)} for ts in scenes]}
