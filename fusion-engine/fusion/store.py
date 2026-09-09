"""Store selection: Neo4j when reachable, in-memory (networkx) otherwise.

Both stores expose the same interface used by FusionState and ReplayState:
    ingest(events, tracks, batch_id, hotspots=None, social_posts=None)
    correlate(event_ids, batch_id, radius_km, window_min, min_severity) -> list[Alert]
    record_fusion(candidates, assessments, clusters, batch_id)
    fusion_candidates|fusion_assessments|fusion_clusters(batch_id, ...)
    alerts(event_ids, batch_id, min_score=0, limit=None) -> list[Alert]
    events(event_ids, conflict_only=False, limit=3000) -> list[dict]
    aircraft(batch_id, military_only=False) -> list[dict]
    tails(batch_id, minutes=30) -> list[dict]
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

from .correlate import Alert
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

    def ingest(self, events: list[OsintEvent], tracks: list[AirTrack], batch_id: str,
               hotspots: list[dict] | None = None, social_posts: dict | None = None):
        for e in events:
            self._events[e.id] = e
        self.batches[batch_id] = {"tracks": list(tracks), "hotspots": list(hotspots or []),
                                  "social_posts": dict(social_posts or {}), "alerts": [],
                                  "candidates": [], "assessments": [], "clusters": [],
                                  "G": None, "gj": None}
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
        for hotspot in b["hotspots"]:
            G.add_node(hotspot["id"], kind="firms", label="FIRMS thermal anomaly",
                       lat=hotspot["lat"], lon=hotspot["lon"], ts=hotspot["ts"],
                       novelty=hotspot.get("novelty", 0), frp=hotspot.get("frp"), satellite=hotspot.get("satellite"))
        for event_id, post in b["social_posts"].items():
            if event_id in G:
                G.nodes[event_id].update(kind="telegram", text=post.text, channel=post.channel,
                                         keywords=post.keywords)
        b["G"], b["alerts"], b["gj"] = G, alerts, None
        return alerts

    def record_fusion(self, candidates, assessments, clusters, batch_id: str):
        """Persist AI pipeline artifacts in the current networkx batch graph."""
        b = self.batches.get(batch_id)
        if not b:
            return
        b["candidates"] = [candidate.to_dict(False) for candidate in candidates]
        b["assessments"] = [assessment.to_dict() for assessment in assessments]
        b["clusters"] = [cluster.to_dict() for cluster in clusters]
        G = b.get("G")
        if G is None:
            return
        for candidate in candidates:
            G.add_node(candidate.id, kind="candidate", label="Proximity candidate",
                       candidate_score=candidate.candidate_score, distance_km=candidate.distance_km,
                       dt_min=candidate.dt_min, batch_id=batch_id)
            if candidate.left.id in G:
                G.add_edge(candidate.id, candidate.left.id, kind="CANDIDATE_MEMBER", role="left")
            if candidate.right.id in G:
                G.add_edge(candidate.id, candidate.right.id, kind="CANDIDATE_MEMBER", role="right")
        for assessment in assessments:
            label = (f"Same article; incident: {assessment.incident_relationship}" if assessment.has_article_match
                     else f"{assessment.verdict}: {assessment.relation}")
            G.add_node(assessment.id, kind="assessment", label=label,
                       **assessment.to_dict(), batch_id=batch_id)
            for role, record_id in (("left", assessment.left_id), ("right", assessment.right_id)):
                if record_id in G:
                    G.add_edge(assessment.id, record_id, kind="ASSESSES", role=role)
            for entity in assessment.resolved_entities:
                canonical = str(entity.get("canonical_name", "")).strip().upper()
                if not canonical:
                    continue
                entity_type = entity.get("entity_type", "OTHER")
                actor_like = entity_type in {"PERSON", "ORGANIZATION", "COUNTRY"}
                entity_id = ("actor:" if actor_like else f"entity:{entity_type}:") + canonical
                G.add_node(entity_id, kind="actor" if actor_like else "entity", label=canonical.title(),
                           entity_type=entity_type, resolution_method="openai")
                record_id = entity.get("record_id")
                if record_id in G:
                    G.add_edge(record_id, entity_id, kind="RESOLVES_TO", confidence=entity.get("confidence", 0),
                               assessment_id=assessment.id)
        for cluster in clusters:
            G.add_node(cluster.id, kind="cluster", label=cluster.brief or "Multi-source fusion cluster",
                       score=cluster.score, modalities=cluster.modalities, needs_review=cluster.needs_review,
                       caveats=cluster.caveats, batch_id=batch_id)
            for record_id in cluster.record_ids:
                if record_id in G:
                    G.add_edge(cluster.id, record_id, kind="CONTAINS")
        b["gj"] = None

    def fusion_candidates(self, batch_id: str | None, limit=300) -> list[dict]:
        b = self.batches.get(batch_id) if batch_id else None
        return list(b.get("candidates", []))[:limit] if b else []

    def fusion_assessments(self, batch_id: str | None, include_rejected=False, limit=300) -> list[dict]:
        b = self.batches.get(batch_id) if batch_id else None
        if not b:
            return []
        values = b.get("assessments", [])
        if not include_rejected:
            values = [value for value in values if value["verdict"] in ("SUPPORTED", "PLAUSIBLE") or value.get("has_article_match")]
        return list(values)[:limit]

    def fusion_clusters(self, batch_id: str | None, limit=100) -> list[dict]:
        b = self.batches.get(batch_id) if batch_id else None
        return list(b.get("clusters", []))[:limit] if b else []

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

    def tails(self, batch_id, minutes: float = 30.0) -> list[dict]:
        """Short position histories for aircraft present in the requested batch."""
        current = self.batches.get(batch_id)
        if not current:
            return []
        from datetime import datetime
        current_ids = {track.id for track in current["tracks"]}
        if not current_ids:
            return []
        newest = max(datetime.fromisoformat(track.ts).timestamp() for track in current["tracks"])
        cutoff = newest - minutes * 60
        points: dict[str, list[AirTrack]] = {track_id: [] for track_id in current_ids}
        for batch in self.batches.values():
            for track in batch["tracks"]:
                if track.id in points and datetime.fromisoformat(track.ts).timestamp() >= cutoff:
                    points[track.id].append(track)
        out = []
        for track_id, history in points.items():
            # A single ingest can obtain the same aircraft from both the military and AOI feeds.
            # One point per timestamp avoids drawing zero-length segments.
            unique = {track.ts: track for track in history}
            ordered = [unique[ts] for ts in sorted(unique)]
            if len(ordered) >= 2:
                last = ordered[-1]
                out.append({"id": track_id, "hex": last.hex, "callsign": last.callsign,
                            "military": last.military, "coords": [[track.lat, track.lon] for track in ordered]})
        return out

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
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    except Exception:
        pass
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
