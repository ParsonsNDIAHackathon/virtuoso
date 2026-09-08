"""Store selection: Neo4j when reachable, in-memory (networkx) otherwise.

Both stores expose the same interface used by FusionState and ReplayState:
    ingest(events, tracks, batch_id)
    correlate(event_ids, batch_id, radius_km, window_min, min_severity) -> list[Alert]
    alerts(event_ids, batch_id, min_score=0, limit=None) -> list[Alert]
    events(event_ids, conflict_only=False, limit=3000) -> list[dict]
    aircraft(batch_id, military_only=False) -> list[dict]
    graph(event_ids, batch_id, max_nodes=220, max_links=400) -> dict
    entity(node_id) -> dict | None
    prune(max_age_h) ; close() ; name

Selection: FUSION_STORE=neo4j|memory|auto (default auto). Auto probes Neo4j once with a short
timeout so a demo laptop without Docker still runs on the in-memory engine.
"""
from __future__ import annotations

import logging
import os
from collections import OrderedDict
from pathlib import Path

from .correlate import Alert, dedupe_alerts
from .correlate_mem import correlate as mem_correlate, graph_to_json
from .ingest_adsb import AirTrack
from .ingest_gdelt import OsintEvent

log = logging.getLogger(__name__)


class InMemoryStore:
    """Batch-scoped in-memory engine. Keeps the last `keep` batches (live ticks or replay minutes)."""
    name = "memory"

    def __init__(self, keep: int = 60):
        self.keep = keep
        self._events: dict[str, OsintEvent] = {}
        self.batches: "OrderedDict[str, dict]" = OrderedDict()

    def ingest(self, events: list[OsintEvent], tracks: list[AirTrack], batch_id: str):
        for e in events:
            self._events[e.id] = e
        self.batches[batch_id] = {"tracks": list(tracks), "alerts": [], "G": None, "gj": None}
        self.batches.move_to_end(batch_id)
        while len(self.batches) > self.keep:
            self.batches.popitem(last=False)
        # drop events no batch references any more (cheap bound on memory)
        if len(self._events) > 200_000:
            self._events.clear()
            for e in events:
                self._events[e.id] = e

    def correlate(self, event_ids, batch_id, radius_km, window_min, min_severity) -> list[Alert]:
        b = self.batches[batch_id]
        ev = [self._events[i] for i in event_ids if i in self._events]
        G, alerts = mem_correlate(ev, b["tracks"], radius_km=radius_km, window_min=window_min, min_severity=min_severity)
        b["G"], b["alerts"], b["gj"] = G, alerts, None
        return alerts

    def alerts(self, event_ids, batch_id, min_score=0.0, limit=None) -> list[Alert]:
        b = self.batches.get(batch_id)
        if not b:
            return []
        ids = set(event_ids)
        out = [a for a in b["alerts"] if a.score >= min_score and a.event_id in ids]
        return out[:limit] if limit else out

    def events(self, event_ids, conflict_only=False, limit=3000) -> list[dict]:
        out = []
        for i in event_ids:
            e = self._events.get(i)
            if e and (e.is_conflict or not conflict_only):
                out.append(e.to_dict())
                if len(out) >= limit:
                    break
        return out

    def aircraft(self, batch_id, military_only=False) -> list[dict]:
        b = self.batches.get(batch_id)
        if not b:
            return []
        return [t.to_dict() for t in b["tracks"] if (t.military or not military_only)]

    def graph(self, event_ids, batch_id, max_nodes=220, max_links=400) -> dict:
        b = self.batches.get(batch_id)
        if not b or b["G"] is None:
            return {"nodes": [], "links": [], "stats": {}}
        if b["gj"] is None:
            b["gj"] = graph_to_json(b["G"], max_nodes=max_nodes)
        gj = b["gj"]
        return {"nodes": gj["nodes"][:max_nodes], "links": gj["links"][:max_links], "stats": gj["stats"]}

    def entity(self, node_id) -> dict | None:
        for b in reversed(self.batches.values()):
            G = b["G"]
            if G is not None and node_id in G:
                links = [{"source": u, "target": v, **d} for u, v, d in G.edges(data=True) if node_id in (u, v)]
                nbr = {v if u == node_id else u for u, v, _ in G.edges(node_id, data=True)} | set(G.predecessors(node_id))
                nodes = {n: {"id": n, **G.nodes[n]} for n in nbr | {node_id}}
                return {"node": nodes[node_id], "links": links, "neighbors": [nodes[n] for n in nbr]}
        return None

    def prune(self, max_age_h: float = 24.0):
        pass

    def close(self):
        pass


def _neo4j_reachable(timeout_s: float = 3.0) -> bool:
    try:
        from neo4j import GraphDatabase
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        drv = GraphDatabase.driver(uri, auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
                                   connection_timeout=timeout_s)
        drv.verify_connectivity()
        drv.close()
        return True
    except Exception as e:
        log.warning("Neo4j not reachable (%s); falling back to in-memory store", str(e)[:120])
        return False


def make_store():
    mode = os.getenv("FUSION_STORE", "auto").lower()
    if mode == "memory":
        log.info("store: in-memory (FUSION_STORE=memory)")
        return InMemoryStore()
    if mode == "neo4j" or _neo4j_reachable():
        from .neo4j_store import Neo4jStore
        s = Neo4jStore()
        s.name = "neo4j"
        log.info("store: Neo4j at %s", os.getenv("NEO4J_URI", "bolt://localhost:7687"))
        return s
    return InMemoryStore()
