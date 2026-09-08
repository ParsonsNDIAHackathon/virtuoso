"""In-memory correlation engine (networkx). Used by InMemoryStore as the no-database fallback.

Multi-INT correlation: entity resolution + spatial/temporal linking into one knowledge graph.

Graph node types
  event     GDELT OSINT event (geocoded, CAMEO coded)
  actor     resolved actor / person / organization from GDELT + GKG
  location  ActionGeo place (resolved by rounded lat/lon + name)
  aircraft  ADS-B track (ICAO hex)
  source    news domain

Edge types
  INVOLVES     event -> actor
  LOCATED_AT   event -> location
  REPORTED_BY  event -> source
  NEAR         aircraft -> event   (spatial proximity within radius_km AND temporal window)
  CO_LOCATED   aircraft -> aircraft (military clusters in same 1-degree cell)

Correlation score for an alert (0..1):
  proximity (closer = higher) * event severity (Goldstein/tone/root) * aircraft weight (military, low alt)
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime

import networkx as nx

from .geo import haversine_km, grid_key, neighbor_keys
from .ingest_adsb import AirTrack
from .ingest_gdelt import OsintEvent

from .correlate import CENTROID_TYPES, Alert, resolve_actor, resolve_location, event_severity, aircraft_weight, dedupe_alerts  # noqa: E402

def _iso_to_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def correlate(events: list[OsintEvent], tracks: list[AirTrack], radius_km: float = 75.0,
              window_min: float = 180.0, min_severity: float = 0.35) -> tuple[nx.MultiDiGraph, list[Alert]]:
    G = nx.MultiDiGraph()
    alerts: list[Alert] = []

    # --- index aircraft by 1-degree grid cell ---
    cell: dict[tuple[int, int], list[AirTrack]] = defaultdict(list)
    for t in tracks:
        cell[grid_key(t.lat, t.lon)].append(t)
        G.add_node(t.id, kind="aircraft", label=t.callsign or t.registration or t.hex,
                   lat=t.lat, lon=t.lon, military=t.military, ac_type=t.ac_type,
                   alt_ft=t.alt_ft, gs_kt=t.gs_kt, ts=t.ts, hex=t.hex)

    # --- events, actors, locations, sources ---
    for e in events:
        sev = event_severity(e)
        G.add_node(e.id, kind="event", label=f"{e.root_label}: {e.place}", lat=e.lat, lon=e.lon,
                   severity=sev, goldstein=e.goldstein, tone=e.tone, root=e.root_label,
                   conflict=e.is_conflict, url=e.url, ts=e.ts, mentions=e.num_mentions)
        loc = resolve_location(e.lat, e.lon, e.place)
        if loc not in G:
            G.add_node(loc, kind="location", label=e.place, lat=e.lat, lon=e.lon)
        G.add_edge(e.id, loc, kind="LOCATED_AT")
        if e.source_domain:
            src = "src:" + e.source_domain
            if src not in G:
                G.add_node(src, kind="source", label=e.source_domain)
            G.add_edge(e.id, src, kind="REPORTED_BY")
        names = [e.actor1, e.actor2] + e.persons[:5] + e.orgs[:5]
        for raw in names:
            a = resolve_actor(raw)
            if not a:
                continue
            aid = "actor:" + a
            if aid not in G:
                G.add_node(aid, kind="actor", label=a.title(), mentions=0)
            G.nodes[aid]["mentions"] += 1
            G.add_edge(e.id, aid, kind="INVOLVES")

        # --- spatial/temporal correlation with aircraft ---
        # skip country/state centroids (geo_type 1,2,5): their coordinates are not a real place
        if sev < min_severity or e.geo_type in CENTROID_TYPES:
            continue
        et = _iso_to_dt(e.ts)
        for key in neighbor_keys(e.lat, e.lon):
            for t in cell.get(key, []):
                d = haversine_km(e.lat, e.lon, t.lat, t.lon)
                if d > radius_km:
                    continue
                dt = abs((_iso_to_dt(t.ts) - et).total_seconds()) / 60.0
                if dt > window_min:
                    continue
                prox = 1.0 - d / radius_km
                score = round(prox * sev * aircraft_weight(t), 3)
                G.add_edge(t.id, e.id, kind="NEAR", distance_km=round(d, 1), dt_min=round(dt, 1), score=score)
                reason = []
                if t.military:
                    reason.append("military aircraft")
                if t.alt_ft is not None and t.alt_ft < 10000:
                    reason.append(f"low altitude {t.alt_ft} ft")
                if e.is_conflict:
                    reason.append(f"conflict-coded event ({e.root_label})")
                if e.goldstein <= -5:
                    reason.append(f"Goldstein {e.goldstein:+.1f}")
                alerts.append(Alert(
                    id=f"{t.id}|{e.id}", score=score, event_id=e.id, aircraft_id=t.id,
                    distance_km=round(d, 1), dt_min=round(dt, 1),
                    event_label=f"{e.root_label}: {e.place}", place=e.place,
                    aircraft_label=f"{t.callsign or t.registration or t.hex} ({t.ac_type or '?'})",
                    lat=e.lat, lon=e.lon, reason="; ".join(reason) or "spatial-temporal proximity",
                ))

    # --- military clustering (aircraft co-located in the same cell) ---
    for key, ts in cell.items():
        mil = [t for t in ts if t.military]
        if len(mil) >= 2:
            for i in range(len(mil)):
                for j in range(i + 1, min(len(mil), i + 6)):
                    G.add_edge(mil[i].id, mil[j].id, kind="CO_LOCATED")

    alerts = dedupe_alerts(alerts)
    alerts.sort(key=lambda a: a.score, reverse=True)
    return G, alerts


def graph_to_json(G: nx.MultiDiGraph, max_nodes: int = 1500) -> dict:
    """Export a trimmed graph for the dashboard: keep alert-connected events, all aircraft in NEAR edges,
    top actors by mentions, and their neighbourhoods."""
    near_edges = [(u, v, d) for u, v, d in G.edges(data=True) if d.get("kind") == "NEAR"]
    keep = set()
    for u, v, _ in near_edges:
        keep.add(u); keep.add(v)
    # add locations/actors/sources hanging off kept events
    for n in list(keep):
        if G.nodes[n].get("kind") == "event":
            keep.update(G.successors(n))
    # top actors overall
    actors = sorted((n for n, d in G.nodes(data=True) if d.get("kind") == "actor"),
                    key=lambda n: -G.nodes[n].get("mentions", 0))[:60]
    keep.update(actors)
    for a in actors:
        keep.update(list(G.predecessors(a))[:8])
    keep = list(keep)[:max_nodes]
    ks = set(keep)
    nodes = [{"id": n, **{k: v for k, v in G.nodes[n].items()}} for n in keep]
    links = [{"source": u, "target": v, **d} for u, v, d in G.edges(data=True) if u in ks and v in ks]
    return {"nodes": nodes, "links": links,
            "stats": {"nodes_total": G.number_of_nodes(), "edges_total": G.number_of_edges(),
                      "near_edges": len(near_edges)}}
