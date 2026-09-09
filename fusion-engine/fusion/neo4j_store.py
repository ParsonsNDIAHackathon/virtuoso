"""Neo4j persistence, correlation, and dashboard projections for the fusion engine."""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase
from neo4j.exceptions import TransientError
from dotenv import load_dotenv

from .correlate import Alert, CENTROID_TYPES, aircraft_weight, dedupe_alerts, event_severity, resolve_actor, resolve_location
from .geo import grid_key, neighbor_keys
from .ingest_adsb import AirTrack
from .ingest_gdelt import OsintEvent

EVENT_FIELDS = {field.name for field in fields(OsintEvent)}
TRACK_FIELDS = {field.name for field in fields(AirTrack)}
log = logging.getLogger(__name__)

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SCHEMA = (
    "CREATE CONSTRAINT event_id IF NOT EXISTS FOR (n:Event) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT actor_id IF NOT EXISTS FOR (n:Actor) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT location_id IF NOT EXISTS FOR (n:Location) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT source_id IF NOT EXISTS FOR (n:Source) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT aircraft_id IF NOT EXISTS FOR (n:Aircraft) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT observation_id IF NOT EXISTS FOR (n:AirObservation) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT thermal_id IF NOT EXISTS FOR (n:ThermalObservation) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT candidate_id IF NOT EXISTS FOR (n:FusionCandidate) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT assessment_id IF NOT EXISTS FOR (n:LLMAssessment) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT cluster_id IF NOT EXISTS FOR (n:FusionCluster) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT resolved_entity_id IF NOT EXISTS FOR (n:ResolvedEntity) REQUIRE n.id IS UNIQUE",
    "CREATE RANGE INDEX event_observed_at IF NOT EXISTS FOR (n:Event) ON (n.observed_at)",
    "CREATE RANGE INDEX observation_grid_time IF NOT EXISTS FOR (n:AirObservation) ON (n.grid, n.observed_at)",
)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _grid(lat: float, lon: float) -> str:
    row, column = grid_key(lat, lon)
    return f"{row}:{column}"


def _neighbor_grids(lat: float, lon: float) -> list[str]:
    return [f"{row}:{column}" for row, column in neighbor_keys(lat, lon)]


def _node_kind(node) -> str:
    labels = set(node.labels)
    if "SocialPost" in labels:
        return "telegram"
    if "Event" in labels:
        return "event"
    if "Actor" in labels:
        return "actor"
    if "ResolvedEntity" in labels:
        return "entity"
    if "Location" in labels:
        return "location"
    if "Source" in labels:
        return "source"
    if "ThermalObservation" in labels:
        return "firms"
    if "LLMAssessment" in labels:
        return "assessment"
    if "FusionCandidate" in labels:
        return "candidate"
    if "FusionCluster" in labels:
        return "cluster"
    return "aircraft"


def _node_payload(node) -> dict[str, Any]:
    props = dict(node)
    return {"id": props["id"], "kind": _node_kind(node), **{
        key: value for key, value in props.items()
        if key not in {"id", "position", "observed_at", "grid", "neighbor_grids", "batch_id", "weight"}
    }}


class Neo4jStore:
    """The live source of truth for fused facts and derived correlations."""

    def __init__(self, uri: str | None = None, user: str | None = None,
                 password: str | None = None, database: str | None = None):
        self.database = database or os.getenv("NEO4J_DATABASE", "neo4j")
        self.driver = GraphDatabase.driver(
            uri or os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            auth=(user or os.getenv("NEO4J_USER", "neo4j"), password or os.getenv("NEO4J_PASSWORD", "neo4jpassword")),
            # Fail a stalled Bolt acquisition instead of allowing the live
            # refresh worker to wait forever.  FusionState additionally has a
            # whole-pass deadline and will use the memory store on failure.
            connection_timeout=float(os.getenv("NEO4J_CONNECT_TIMEOUT_S", "10")),
            connection_acquisition_timeout=float(os.getenv("NEO4J_ACQUIRE_TIMEOUT_S", "10")),
            max_transaction_retry_time=0,
        )
        self._schema_ready = False

    def close(self):
        self.driver.close()

    def _query(self, query: str, **parameters):
        retries = max(0, int(os.getenv("NEO4J_DEADLOCK_RETRIES", "3")))
        for attempt in range(retries + 1):
            try:
                records, _, _ = self.driver.execute_query(
                    query, parameters_=parameters, database_=self.database,
                )
                return records
            except TransientError as error:
                code = str(getattr(error, "code", "") or getattr(error, "neo4j_code", ""))
                if not code.endswith(".DeadlockDetected") or attempt >= retries:
                    raise
                delay = 0.05 * (2 ** attempt)
                log.warning("Neo4j deadlock; retrying query in %.2fs (%d/%d)",
                            delay, attempt + 1, retries)
                time.sleep(delay)
        raise AssertionError("unreachable")

    def ensure_schema(self):
        if self._schema_ready:
            return
        for statement in SCHEMA:
            self._query(statement)
        self._schema_ready = True

    def ingest(self, events: list[OsintEvent], tracks: list[AirTrack], batch_id: str,
               hotspots: list[dict] | None = None, social_posts: dict | None = None):
        """Idempotently persist source facts and their entity relationships."""
        self.ensure_schema()
        event_rows = []
        actor_rows = []
        social_posts = social_posts or {}
        for event in events:
            props = asdict(event)
            post = social_posts.get(event.id)
            if post:
                props.update(source_kind="telegram", text=post.text[:8000], channel=post.channel,
                             keywords=post.keywords, views=post.views, has_media=post.has_media)
            else:
                props["source_kind"] = "gdelt"
            event_rows.append({
                "id": event.id, "props": props, "observed_at": _dt(event.ts),
                "severity": event_severity(event), "lat": event.lat, "lon": event.lon,
                "location_id": resolve_location(event.lat, event.lon, event.place),
                "neighbor_grids": _neighbor_grids(event.lat, event.lon), "social": bool(post),
            })
            for raw_name in [event.actor1, event.actor2, *event.persons[:5], *event.orgs[:5]]:
                name = resolve_actor(raw_name)
                if name:
                    actor_rows.append({"event_id": event.id, "id": f"actor:{name}", "label": name.title()})
        if event_rows:
            self._query(
                """
                UNWIND $rows AS row
                MERGE (e:Event {id: row.id})
                SET e += row.props, e.observed_at = row.observed_at, e.severity = row.severity,
                    e.position = point({latitude: row.lat, longitude: row.lon}), e.neighbor_grids = row.neighbor_grids,
                    e.label = e.root_label + ': ' + e.place
                FOREACH (_ IN CASE WHEN row.social THEN [1] ELSE [] END | SET e:SocialPost)
                MERGE (l:Location {id: row.location_id})
                ON CREATE SET l.label = e.place, l.lat = e.lat, l.lon = e.lon
                MERGE (e)-[:LOCATED_AT {kind: 'LOCATED_AT'}]->(l)
                FOREACH (_ IN CASE WHEN e.source_domain = '' THEN [] ELSE [1] END |
                    MERGE (s:Source {id: 'src:' + e.source_domain})
                    ON CREATE SET s.label = e.source_domain
                    MERGE (e)-[:REPORTED_BY {kind: 'REPORTED_BY'}]->(s)
                )
                """, rows=event_rows,
            )
        if actor_rows:
            self._query(
                """
                UNWIND $rows AS row
                MATCH (e:Event {id: row.event_id})
                MERGE (a:Actor {id: row.id})
                ON CREATE SET a.label = row.label
                MERGE (e)-[:INVOLVES {kind: 'INVOLVES'}]->(a)
                """, rows=actor_rows,
            )

        track_rows = []
        for track in tracks:
            track_props = asdict(track)
            track_rows.append({
                # An aircraft is a durable identity; an observation is one
                # position report in a specific ingest batch.  Keep their IDs
                # distinct so repeated pulls cannot violate the observation
                # uniqueness constraint.
                "id": f"{batch_id}|{track.id}", "aircraft_id": track.id, "props": track_props,
                "observation_props": {key: value for key, value in track_props.items() if key != "id"},
                "observed_at": _dt(track.ts), "lat": track.lat, "lon": track.lon,
                "grid": _grid(track.lat, track.lon), "weight": aircraft_weight(track), "batch_id": batch_id,
            })
        if track_rows:
            self._query(
                """
                UNWIND $rows AS row
                MERGE (a:Aircraft {id: row.aircraft_id})
                SET a += row.props, a.label = coalesce(row.props.callsign, row.props.registration, row.props.hex)
                MERGE (o:AirObservation {id: row.id})
                SET o += row.observation_props, o.aircraft_id = row.aircraft_id,
                    o.observed_at = row.observed_at, o.position = point({latitude: row.lat, longitude: row.lon}),
                    o.grid = row.grid, o.weight = row.weight, o.batch_id = row.batch_id
                MERGE (a)-[r:OBSERVED_AS]->(o)
                SET r.kind = 'OBSERVED_AS'
                """, rows=track_rows,
            )

        hotspot_rows = []
        for hotspot in hotspots or []:
            hotspot_rows.append({
                "id": hotspot["id"], "props": hotspot, "observed_at": _dt(hotspot["ts"]),
                "lat": hotspot["lat"], "lon": hotspot["lon"], "batch_id": batch_id,
            })
        if hotspot_rows:
            self._query(
                """
                UNWIND $rows AS row
                MERGE (h:ThermalObservation {id: row.id})
                SET h += row.props, h.observed_at = row.observed_at, h.batch_id = row.batch_id,
                    h.position = point({latitude: row.lat, longitude: row.lon}),
                    h.label = 'FIRMS thermal anomaly'
                """, rows=hotspot_rows,
            )

    def record_fusion(self, candidates, assessments, clusters, batch_id: str):
        """Persist candidate generation, LLM adjudication, entity resolution and clusters."""
        candidate_rows = [{**candidate.to_dict(False), "batch_id": batch_id} for candidate in candidates]
        if candidate_rows:
            self._query(
                """
                UNWIND $rows AS row
                MERGE (c:FusionCandidate {id: row.id})
                SET c += row, c.label = 'Proximity candidate'
                WITH c, row
                MATCH (left {id: row.left_id}), (right {id: row.right_id})
                MERGE (c)-[cl:CANDIDATE_MEMBER {role: 'left'}]->(left) SET cl.kind = 'CANDIDATE_MEMBER'
                MERGE (c)-[cr:CANDIDATE_MEMBER {role: 'right'}]->(right) SET cr.kind = 'CANDIDATE_MEMBER'
                """, rows=candidate_rows,
            )
        assessment_rows = []
        entity_rows = []
        for assessment in assessments:
            value = assessment.to_dict()
            assessment_rows.append({
                **{key: value[key] for key in (
                    "id", "candidate_id", "left_id", "left_kind", "right_id", "right_kind",
                    "verdict", "relation", "evidence_strength", "supporting_facts",
                    "strongest_limitation", "rationale", "model", "prompt_version", "created_at",
                    "cached", "distance_km", "dt_min", "needs_review", "incident_relationship", "has_article_match")},
                "resolved_entities_json": json.dumps(value["resolved_entities"], ensure_ascii=False),
                "article_match_json": json.dumps(value["article_match"], ensure_ascii=False),
                "source_documents_json": json.dumps(value["source_documents"], ensure_ascii=False),
                "source_groups_json": json.dumps(value["source_groups"], ensure_ascii=False),
                "batch_id": batch_id,
            })
            for entity in assessment.resolved_entities:
                canonical = str(entity.get("canonical_name", "")).strip().upper()
                if canonical and entity.get("record_id") in {assessment.left_id, assessment.right_id}:
                    entity_type = entity.get("entity_type", "OTHER")
                    actor_like = entity_type in {"PERSON", "ORGANIZATION", "COUNTRY"}
                    entity_rows.append({
                        "record_id": entity["record_id"],
                        "entity_id": ("actor:" if actor_like else f"entity:{entity_type}:") + canonical,
                        "label": canonical.title(), "confidence": entity.get("confidence", 0),
                        "entity_type": entity_type, "actor_like": actor_like, "assessment_id": assessment.id,
                    })
        if assessment_rows:
            self._query(
                """
                UNWIND $rows AS row
                MERGE (a:LLMAssessment {id: row.id})
                SET a += row, a.label = CASE WHEN row.has_article_match THEN 'Same article; incident: ' + row.incident_relationship
                    ELSE row.verdict + ': ' + row.relation END
                WITH a, row
                MATCH (left {id: row.left_id}), (right {id: row.right_id})
                MERGE (a)-[al:ASSESSES {role: 'left'}]->(left) SET al.kind = 'ASSESSES'
                MERGE (a)-[ar:ASSESSES {role: 'right'}]->(right) SET ar.kind = 'ASSESSES'
                """, rows=assessment_rows,
            )
        if entity_rows:
            self._query(
                """
                UNWIND $rows AS row
                MATCH (record {id: row.record_id})
                MERGE (entity {id: row.entity_id})
                SET entity:ResolvedEntity, entity.label = row.label, entity.entity_type = row.entity_type
                FOREACH (_ IN CASE WHEN row.actor_like THEN [1] ELSE [] END | SET entity:Actor)
                MERGE (record)-[r:RESOLVES_TO {assessment_id: row.assessment_id, entity_id: row.entity_id}]->(entity)
                SET r.kind = 'RESOLVES_TO', r.confidence = row.confidence, r.entity_type = row.entity_type,
                    r.resolution_method = 'openai'
                """, rows=entity_rows,
            )
        cluster_rows = [{**cluster.to_dict(), "batch_id": batch_id} for cluster in clusters]
        if cluster_rows:
            self._query(
                """
                UNWIND $rows AS row
                MERGE (c:FusionCluster {id: row.id})
                SET c += row, c.label = coalesce(row.brief, 'Multi-source fusion cluster')
                WITH c, row UNWIND row.record_ids AS record_id
                MATCH (record {id: record_id})
                MERGE (c)-[r:CONTAINS]->(record) SET r.kind = 'CONTAINS'
                """, rows=cluster_rows,
            )

    def fusion_candidates(self, batch_id: str | None, limit=300) -> list[dict]:
        if not batch_id:
            return []
        records = self._query(
            "MATCH (c:FusionCandidate {batch_id: $batch_id}) RETURN c ORDER BY c.candidate_score DESC LIMIT $limit",
            batch_id=batch_id, limit=limit,
        )
        return [dict(record["c"]) for record in records]

    def fusion_assessments(self, batch_id: str | None, include_rejected=False, limit=300) -> list[dict]:
        if not batch_id:
            return []
        records = self._query(
            """
            MATCH (a:LLMAssessment {batch_id: $batch_id})
            WHERE $include_rejected OR a.verdict IN ['SUPPORTED', 'PLAUSIBLE'] OR a.has_article_match = true
            RETURN a ORDER BY a.evidence_strength DESC LIMIT $limit
            """, batch_id=batch_id, include_rejected=include_rejected, limit=limit,
        )
        out = []
        for record in records:
            value = dict(record["a"])
            value["resolved_entities"] = json.loads(value.pop("resolved_entities_json", "[]"))
            for field, default in (("article_match", "{}"), ("source_documents", "[]"), ("source_groups", "{}")):
                value[field] = json.loads(value.pop(field + "_json", default))
            out.append(value)
        return out

    def fusion_clusters(self, batch_id: str | None, limit=100) -> list[dict]:
        if not batch_id:
            return []
        records = self._query(
            "MATCH (c:FusionCluster {batch_id: $batch_id}) RETURN c ORDER BY c.score DESC LIMIT $limit",
            batch_id=batch_id, limit=limit,
        )
        return [dict(record["c"]) for record in records]

    def correlate(self, event_ids: list[str], batch_id: str, radius_km: float,
                  window_min: float, min_severity: float) -> list[Alert]:
        """Create database-resident NEAR and CO_LOCATED relationships for one ADS-B batch."""
        if not event_ids:
            return []
        self._query(
            """
            MATCH (e:Event)
            WHERE e.id IN $event_ids AND e.severity >= $min_severity AND NOT e.geo_type IN $centroid_types
            UNWIND e.neighbor_grids AS grid
            MATCH (o:AirObservation {batch_id: $batch_id, grid: grid})
            WHERE o.observed_at >= e.observed_at - duration({minutes: $window_min})
              AND o.observed_at <= e.observed_at + duration({minutes: $window_min})
            WITH DISTINCT e, o, point.distance(e.position, o.position) / 1000.0 AS distance_km
            WHERE distance_km <= $radius_km
            MATCH (a:Aircraft)-[:OBSERVED_AS]->(o)
            WITH a, e, o, distance_km,
                 abs(duration.inSeconds(e.observed_at, o.observed_at).seconds) / 60.0 AS dt_min,
                 (1.0 - distance_km / $radius_km) * e.severity * o.weight AS score
            MERGE (a)-[r:NEAR {observation_id: o.id}]->(e)
            SET r.kind = 'NEAR', r.batch_id = $batch_id, r.distance_km = round(distance_km, 1),
                r.dt_min = round(dt_min, 1), r.score = round(score, 3)
            """,
            event_ids=event_ids, batch_id=batch_id, radius_km=radius_km,
            window_min=window_min, min_severity=min_severity, centroid_types=list(CENTROID_TYPES),
        )
        self._query(
            """
            MATCH (a:Aircraft)-[:OBSERVED_AS]->(left:AirObservation {batch_id: $batch_id}),
                  (b:Aircraft)-[:OBSERVED_AS]->(right:AirObservation {batch_id: $batch_id})
            WHERE left.military = true AND right.military = true AND left.grid = right.grid AND a.id < b.id
            MERGE (a)-[r:CO_LOCATED {batch_id: $batch_id}]->(b)
            SET r.kind = 'CO_LOCATED'
            """, batch_id=batch_id,
        )
        return self.alerts(event_ids, batch_id)

    def alerts(self, event_ids: list[str], batch_id: str, min_score: float = 0.0,
               limit: int | None = None) -> list[Alert]:
        records = self._query(
            """
            MATCH (a:Aircraft)-[r:NEAR {batch_id: $batch_id}]->(e:Event)
            WHERE e.id IN $event_ids AND r.score >= $min_score
            RETURN a, e, r ORDER BY r.score DESC
            """, event_ids=event_ids, batch_id=batch_id, min_score=min_score,
        )
        alerts = []
        for record in records:
            aircraft, event, relation = record["a"], record["e"], record["r"]
            reason = []
            if aircraft.get("military"):
                reason.append("military aircraft")
            if aircraft.get("alt_ft") is not None and aircraft["alt_ft"] < 10000:
                reason.append(f"low altitude {aircraft['alt_ft']} ft")
            if event.get("is_conflict"):
                reason.append(f"conflict-coded event ({event['root_label']})")
            if event.get("goldstein", 0) <= -5:
                reason.append(f"Goldstein {event['goldstein']:+.1f}")
            alerts.append(Alert(
                id=f"{aircraft['id']}|{event['id']}", score=relation["score"], event_id=event["id"],
                aircraft_id=aircraft["id"], distance_km=relation["distance_km"], dt_min=relation["dt_min"],
                event_label=event["label"], place=event["place"],
                aircraft_label=f"{aircraft.get('callsign') or aircraft.get('registration') or aircraft['hex']} ({aircraft.get('ac_type') or '?'})",
                lat=event["lat"], lon=event["lon"], reason="; ".join(reason) or "spatial-temporal proximity",
            ))
        output = sorted(dedupe_alerts(alerts), key=lambda alert: alert.score, reverse=True)
        return output[:limit] if limit else output

    def events(self, event_ids: list[str], conflict_only: bool = False, limit: int = 3000) -> list[dict]:
        if not event_ids:
            return []
        records = self._query(
            """
            MATCH (e:Event) WHERE e.id IN $event_ids AND ($conflict_only = false OR e.is_conflict = true)
            RETURN e ORDER BY e.observed_at DESC LIMIT $limit
            """, event_ids=event_ids, conflict_only=conflict_only, limit=limit,
        )
        return [{key: value for key, value in dict(record["e"]).items() if key in EVENT_FIELDS} for record in records]

    def aircraft(self, batch_id: str | None, military_only: bool = False) -> list[dict]:
        if not batch_id:
            return []
        records = self._query(
            """
            MATCH (a:Aircraft)-[:OBSERVED_AS]->(o:AirObservation {batch_id: $batch_id})
            WHERE $military_only = false OR o.military = true
            RETURN o, a.id AS aircraft_id ORDER BY o.military DESC, o.hex
            """, batch_id=batch_id, military_only=military_only,
        )
        out = []
        for record in records:
            track = {key: value for key, value in dict(record["o"]).items() if key in TRACK_FIELDS}
            # API clients join alert.aircraft_id to track.id.  The Neo4j node's
            # own ID is intentionally an observation ID, not that aircraft ID.
            track["id"] = record["aircraft_id"]
            out.append(track)
        return out

    def tails(self, batch_id: str | None, minutes: float = 30.0) -> list[dict]:
        """Return recent paths for aircraft that appear in the current live batch."""
        if not batch_id:
            return []
        records = self._query(
            """
            MATCH (current:AirObservation {batch_id: $batch_id})
            WITH collect(DISTINCT current.aircraft_id) AS aircraft_ids,
                 max(current.observed_at) AS newest
            MATCH (a:Aircraft)-[:OBSERVED_AS]->(o:AirObservation)
            WHERE a.id IN aircraft_ids
              AND o.observed_at >= newest - duration({minutes: $minutes})
              AND o.observed_at <= newest
            WITH a, o ORDER BY a.id, o.observed_at
            RETURN a.id AS id, a.hex AS hex, a.callsign AS callsign, a.military AS military,
                   collect([o.lat, o.lon]) AS coords
            """, batch_id=batch_id, minutes=minutes,
        )
        return [dict(record) for record in records if len(record["coords"]) >= 2]

    def graph(self, event_ids: list[str], batch_id: str | None, max_nodes: int = 220,
              max_links: int = 400) -> dict:
        """Return a bounded projection suitable for an interactive browser graph.

        Neo4j retains the complete graph.  This endpoint deliberately returns only a
        compact, score-ordered view: a force layout with thousands of nodes and
        edges is not useful (or responsive) in a browser.
        """
        nodes: dict[str, dict] = {}
        links: list[dict] = []

        def add_node(node):
            payload = _node_payload(node)
            if len(nodes) < max_nodes or payload["id"] in nodes:
                nodes[payload["id"]] = payload

        if batch_id:
            near = self._query(
                """
                MATCH (a:Aircraft)-[r:NEAR {batch_id: $batch_id}]->(e:Event) WHERE e.id IN $event_ids
                RETURN a, e, r ORDER BY r.score DESC
                """, batch_id=batch_id, event_ids=event_ids,
            )
            for record in near:
                add_node(record["a"]); add_node(record["e"])
                links.append({"source": record["a"]["id"], "target": record["e"]["id"], **dict(record["r"])})
            clusters = self._query(
                "MATCH (a:Aircraft)-[r:CO_LOCATED {batch_id: $batch_id}]->(b:Aircraft) RETURN a, b, r",
                batch_id=batch_id,
            )
            for record in clusters:
                add_node(record["a"]); add_node(record["b"])
                links.append({"source": record["a"]["id"], "target": record["b"]["id"], **dict(record["r"])})

        base = self._query(
            """
            MATCH (e:Event) WHERE e.id IN $event_ids
            OPTIONAL MATCH (e)-[r]->(n)
            WHERE type(r) IN ['INVOLVES', 'LOCATED_AT', 'REPORTED_BY']
            RETURN e, r, n
            """, event_ids=event_ids,
        ) if event_ids else []
        for record in base:
            add_node(record["e"])
            if record["n"] is not None:
                add_node(record["n"])
                links.append({"source": record["e"]["id"], "target": record["n"]["id"], **dict(record["r"])})

        if batch_id:
            ai = self._query(
                """
                MATCH (n) WHERE n.batch_id = $batch_id AND
                    (n:FusionCluster OR (n:LLMAssessment AND
                        (n.verdict IN ['SUPPORTED', 'PLAUSIBLE'] OR n.has_article_match = true)))
                OPTIONAL MATCH (n)-[r]->(m)
                WHERE type(r) IN ['ASSESSES', 'CONTAINS']
                RETURN n, r, m ORDER BY coalesce(n.score, n.evidence_strength, 0) DESC LIMIT 250
                """, batch_id=batch_id,
            )
            for record in ai:
                add_node(record["n"])
                if record["m"] is not None:
                    add_node(record["m"])
                    links.append({"source": record["n"]["id"], "target": record["m"]["id"], **dict(record["r"])})
            resolutions = self._query(
                """
                MATCH (assessment:LLMAssessment {batch_id: $batch_id})
                WHERE assessment.verdict IN ['SUPPORTED', 'PLAUSIBLE']
                MATCH (record)-[r:RESOLVES_TO]->(entity)
                WHERE r.assessment_id = assessment.id
                RETURN record, r, entity ORDER BY r.confidence DESC LIMIT 100
                """, batch_id=batch_id,
            )
            for record in resolutions:
                add_node(record["record"]); add_node(record["entity"])
                links.append({"source": record["record"]["id"], "target": record["entity"]["id"],
                              **dict(record["r"])})

        keep = set(nodes)
        # `near` is loaded first and score-sorted, so truncation preserves the
        # strongest analyst-relevant correlations before supporting context.
        links = [link for link in links if link["source"] in keep and link["target"] in keep][:max_links]
        return {"nodes": list(nodes.values()), "links": links,
                "stats": {"nodes_total": len(nodes), "edges_total": len(links),
                          "near_edges": sum(link.get("kind") == "NEAR" for link in links),
                          "assessments": sum(node.get("kind") == "assessment" for node in nodes.values()),
                          "clusters": sum(node.get("kind") == "cluster" for node in nodes.values())}}

    def prune(self, max_age_h: float = 24.0, keep_replay: bool = True):
        """Retention: drop AirObservation nodes (and their NEAR/CO_LOCATED edges) older than
        max_age_h. Live batches accumulate ~1-2k observations per minute, so without this the
        graph grows without bound. Replay batches are kept by default (bounded by distinct minutes)."""
        cutoff = datetime.now(timezone.utc) - __import__("datetime").timedelta(hours=max_age_h)
        self._query(
            """
            MATCH (o:AirObservation) WHERE o.observed_at < $cutoff
              AND ($keep_replay = false OR NOT o.batch_id STARTS WITH 'replay:')
            WITH o LIMIT 50000
            OPTIONAL MATCH (a:Aircraft)-[n:NEAR {observation_id: o.id}]->()
            DELETE n
            WITH DISTINCT o
            DETACH DELETE o
            """, cutoff=cutoff, keep_replay=keep_replay,
        )
        self._query(
            "MATCH ()-[r:CO_LOCATED]->() WHERE NOT EXISTS { MATCH (:AirObservation {batch_id: r.batch_id}) } DELETE r"
        )

    def entity(self, node_id: str) -> dict | None:
        records = self._query(
            """
            MATCH (n {id: $node_id})
            OPTIONAL MATCH (n)-[r]-(m)
            WHERE type(r) IN ['INVOLVES', 'LOCATED_AT', 'REPORTED_BY', 'NEAR', 'CO_LOCATED',
                              'CANDIDATE_MEMBER', 'ASSESSES', 'RESOLVES_TO', 'CONTAINS']
            RETURN n, r, m, startNode(r) AS source, endNode(r) AS target LIMIT 250
            """, node_id=node_id,
        )
        if not records:
            return None
        node = _node_payload(records[0]["n"])
        neighbors: dict[str, dict] = {}
        links = []
        for record in records:
            if record["m"] is None:
                continue
            neighbor = _node_payload(record["m"])
            neighbors[neighbor["id"]] = neighbor
            relation = record["r"]
            links.append({"source": record["source"]["id"], "target": record["target"]["id"], **dict(relation)})
        return {"node": node, "links": links, "neighbors": list(neighbors.values())}
