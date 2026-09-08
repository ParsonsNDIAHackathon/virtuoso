"""Parity check: feed the same events + tracks to the in-memory store and the Neo4j store and
compare the alert lists. Run with Neo4j up:

    python -m tests.parity_stores data/snapshot.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from fusion.ingest_adsb import AirTrack
from fusion.ingest_gdelt import OsintEvent
from fusion.store import InMemoryStore


def load(path: Path):
    snap = json.loads(path.read_text(encoding="utf-8"))
    ev_fields = OsintEvent.__dataclass_fields__.keys()
    tr_fields = AirTrack.__dataclass_fields__.keys()
    events = [OsintEvent(**{k: v for k, v in e.items() if k in ev_fields}) for e in snap["events"]]
    tracks = [AirTrack(**{k: v for k, v in t.items() if k in tr_fields}) for t in snap["tracks"]]
    return events, tracks


def run(store, events, tracks, batch):
    t0 = time.time()
    store.ingest(events, tracks, batch)
    alerts = store.correlate([e.id for e in events], batch, radius_km=75.0, window_min=240.0, min_severity=0.35)
    return alerts, time.time() - t0


def main():
    path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/snapshot.json")
    events, tracks = load(path)
    print(f"{len(events)} events, {len(tracks)} tracks from {path}")
    mem, t_mem = run(InMemoryStore(), events, tracks, "parity:mem")
    print(f"memory: {len(mem)} alerts in {t_mem:.1f}s, top={mem[0].score if mem else None}")
    try:
        from fusion.neo4j_store import Neo4jStore
        neo = Neo4jStore()
        n4, t_neo = run(neo, events, tracks, "parity:neo")
        print(f"neo4j : {len(n4)} alerts in {t_neo:.1f}s, top={n4[0].score if n4 else None}")
    except Exception as e:
        print("neo4j unavailable:", str(e)[:120])
        return
    key = lambda a: (a.aircraft_id, round(a.lat, 1), round(a.lon, 1))
    m = {key(a): a for a in mem}
    n = {key(a): a for a in n4}
    only_m, only_n, both = set(m) - set(n), set(n) - set(m), set(m) & set(n)
    diffs = [(k, m[k].score, n[k].score) for k in both if abs(m[k].score - n[k].score) > 0.02]
    print(f"pairs: both={len(both)} memory-only={len(only_m)} neo4j-only={len(only_n)} score-diff>0.02={len(diffs)}")
    for k, a, b in sorted(diffs, key=lambda x: -abs(x[1] - x[2]))[:8]:
        print("  ", k, a, b)
    top_m = [key(a) for a in mem[:10]]
    top_n = [key(a) for a in n4[:10]]
    print("top-10 overlap:", len(set(top_m) & set(top_n)))
    neo.close()


if __name__ == "__main__":
    main()
