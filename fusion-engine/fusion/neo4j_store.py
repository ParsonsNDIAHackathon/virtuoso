"""Neo4j persistence, correlation, and dashboard projections for the fusion engine."""
from __future__ import annotations

import os
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase
from dotenv import load_dotenv

from .correlate import Alert, CENTROID_TYPES, aircraft_weight, dedupe_alerts, event_severity, resolve_actor, resolve_location
from .geo import grid_key, neighbor_keys
from .ingest_adsb import AirTrack
from .ingest_gdelt import OsintEvent

EVENT_FIELDS = {field.name for field in fields(OsintEvent)}
TRACK_FIELDS = {field.name for field in fields(AirTrack)}

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

SCHEMA = (
    "CREATE CONSTRAINT event_id IF NOT EXISTS FOR (n:Event) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT actor_id IF NOT EXISTS FOR (n:Actor) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT location_id IF NOT EXISTS FOR (n:Location) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT source_id IF NOT EXISTS FOR (n:Source) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT aircraft_id IF NOT EXISTS FOR (n:Aircraft) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT observation_id IF NOT EXISTS FOR (n:AirObservation) REQUIRE n.id IS UNIQUE",
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
    if "Event" in labels:
        return "event"
    if "Actor" in labels:
        return "actor"
    if "Location" in labels:
        return "location"
    if "Source" in labels:
        return "source"
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
        )
        self._schema_ready = False

    def close(self):
        self.driver.close()

    def _query(self, query: str, **parameters):
        records, _, _ = self.driver.execute_query(query, parameters_=parameters, database_=self.database)
        return records

    def ensure_schema(self):
        if self._schema_ready:
            return
        for statement in SCHEMA:
            self._query(statement)
        self._schema_ready = True

    def ingest(self, events: list[OsintEvent], tracks: list[AirTrack], batch_id: str):
        """Idempotently persist source facts and their entity relationships."""
        self.ensure_schema()
        event_rows = []
        actor_rows = []
        for event in events:
            event_rows.append({
                "id": event.id, "props": asdict(event), "observed_at": _dt(event.ts),
                "severity": event_severity(event), "lat": event.lat, "lon": event.lon,
                "location_id": resolve_location(event.lat, event.lon, event.place),
                "neighbor_grids": _neighbor_grids(event.lat, event.lon),
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
            track_rows.append({
                "id": f"{track.id}|{track.ts}", "aircraft_id": track.id, "props": asdict(track),
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
                SET o += row.props, o.observed_at = row.observed_at, o.position = point({latitude: row.lat, longitude: row.lon}),
                    o.grid = row.grid, o.weight = row.weight, o.batch_id = row.batch_id
                MERGE (a)-[:OBSERVED_AS]->(o)
                """, rows=track_rows,
            )

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
            MATCH (:Aircraft)-[:OBSERVED_AS]->(o:AirObservation {batch_id: $batch_id})
            WHERE $military_only = false OR o.military = true
            RETURN o ORDER BY o.military DESC, o.hex
            """, batch_id=batch_id, military_only=military_only,
        )
        return [{key: value for key, value in dict(record["o"]).items() if key in TRACK_FIELDS} for record in records]

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

        keep = set(nodes)
        # `near` is loaded first and score-sorted, so truncation preserves the
        # strongest analyst-relevant correlations before supporting context.
        links = [link for link in links if link["source"] in keep and link["target"] in keep][:max_links]
        return {"nodes": list(nodes.values()), "links": links,
                "stats": {"nodes_total": len(nodes), "edges_total": len(links),
                          "near_edges": sum(link.get("kind") == "NEAR" for link in links)}}

    def entity(self, node_id: str) -> dict | None:
        records = self._query(
            """
            MATCH (n {id: $node_id})
            OPTIONAL MATCH (n)-[r]-(m)
            WHERE type(r) IN ['INVOLVES', 'LOCATED_AT', 'REPORTED_BY', 'NEAR', 'CO_LOCATED']
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
