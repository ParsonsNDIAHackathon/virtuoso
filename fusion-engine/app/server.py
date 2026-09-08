"""FastAPI dashboard + JSON API for the Multi-INT Fusion Engine.

    uvicorn app.server:app --reload --port 8000
    open http://localhost:8000
"""
from __future__ import annotations

import logging
import threading
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from fusion.pipeline import FusionState, run_loop, run_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="Multi-INT Fusion Engine", version="0.1")
state = FusionState()
_worker: threading.Thread | None = None


def _parse_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    """Parse minLon,minLat,maxLon,maxLat; minLon may exceed maxLon across the dateline."""
    if bbox is None:
        return None
    try:
        west, south, east, north = (float(value) for value in bbox.split(","))
    except (TypeError, ValueError):
        raise HTTPException(422, "bbox must be minLon,minLat,maxLon,maxLat") from None
    if not (-180 <= west <= 180 and -180 <= east <= 180 and -90 <= south <= 90 and -90 <= north <= 90 and south <= north):
        raise HTTPException(422, "bbox coordinates are out of range")
    return west, south, east, north


def _in_view(records: list[dict], bbox: str | None, limit: int) -> list[dict]:
    bounds = _parse_bbox(bbox)
    if bounds is None:
        return records[:limit]
    west, south, east, north = bounds

    def contains(record: dict) -> bool:
        lat, lon = record.get("lat"), record.get("lon")
        if lat is None or lon is None or not south <= lat <= north:
            return False
        return west <= lon <= east if west <= east else lon >= west or lon <= east

    return [record for record in records if contains(record)][:limit]


@app.on_event("startup")
def _startup():
    global _worker
    # Serve immediately; the first fuse runs in the background (primed=False -> fetch now).
    # The UI shows "warming up" until /api/status reports an `updated` timestamp.
    _worker = threading.Thread(target=run_loop, args=(state,), kwargs={"windows": 2, "primed": False}, daemon=True)
    _worker.start()


@app.get("/api/status")
def status():
    return state.api_status()


@app.get("/api/alerts")
def alerts(limit: int = Query(100, le=2000), min_score: float = 0.0):
    return state.api_alerts(min_score, limit)


@app.get("/api/events")
def events(conflict_only: bool = False, limit: int = Query(3000, ge=1, le=20000), bbox: str | None = None):
    # Retrieve before filtering to preserve the existing API's result semantics.
    return _in_view(state.api_events(conflict_only, 20000), bbox, limit)


@app.get("/api/aircraft")
def aircraft(military_only: bool = False, limit: int = Query(3000, ge=1, le=20000), bbox: str | None = None):
    return _in_view(state.api_aircraft(military_only), bbox, limit)


@app.get("/api/timeline")
def timeline(hours: float = Query(24.0, ge=0, le=24 * 30)):
    """Per-fuse activity counts for the last `hours` (0 = all persisted history)."""
    return state.api_timeline(hours)


@app.get("/api/firms")
def firms(limit: int = Query(3000, ge=1, le=20000), bbox: str | None = None):
    with state.lock:
        return _in_view(list(state.firms), bbox, limit)


@app.get("/api/graph")
def graph(max_nodes: int = Query(220, ge=25, le=500), max_links: int = Query(400, ge=50, le=1000)):
    """Compact graph projection for the browser; Neo4j retains the full graph."""
    return state.api_graph(max_nodes=max_nodes, max_links=max_links)


@app.post("/api/refresh")
def refresh():
    """Force an immediate ADS-B pull + re-fuse (GDELT stays on its 15-min cadence)."""
    try:
        state.refresh_adsb()
        state.fuse()
        state.save()
        return status()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/entity/{node_id:path}")
def entity(node_id: str):
    """Neo4j neighbourhood of one graph node (for click-through in the UI)."""
    result = state.api_entity(node_id)
    if result is None:
        return JSONResponse({"error": "unknown node"}, status_code=404)
    return result


@app.on_event("shutdown")
def _shutdown():
    state.close()
    for replay in _replays.values():
        replay.store.close()


# ---------------- areas of interest (circles) ----------------
from pydantic import BaseModel  # noqa: E402


class RegionIn(BaseModel):
    lat: float
    lon: float
    radius_nm: float = 100.0
    name: str | None = None


@app.get("/api/regions")
def regions():
    with state.lock:
        return list(state.regions)


@app.post("/api/regions")
def add_region(r: RegionIn):
    return state.add_region(r.lat, r.lon, r.radius_nm, r.name)


@app.delete("/api/regions/{rid}")
def delete_region(rid: str):
    if not state.remove_region(rid):
        return JSONResponse({"error": "unknown region"}, status_code=404)
    return {"ok": True}


# ---------------- replay mode ----------------
from fusion.replay import SCENARIOS, ReplayState  # noqa: E402

_replays: dict[str, ReplayState] = {}


def _replay(scenario: str) -> ReplayState:
    if scenario not in SCENARIOS:
        raise KeyError(scenario)
    if scenario not in _replays:
        _replays[scenario] = ReplayState(scenario)
    return _replays[scenario]


@app.get("/api/replay/scenarios")
def replay_scenarios():
    return [{"id": k, **v} for k, v in SCENARIOS.items()]


@app.get("/api/replay/{scenario}/config")
def replay_config(scenario: str):
    try:
        return _replay(scenario).config()
    except KeyError:
        return JSONResponse({"error": "unknown scenario"}, status_code=404)


@app.get("/api/replay/{scenario}/timeline")
def replay_timeline(scenario: str, step_min: int = 15):
    return _replay(scenario).timeline(step_min)


@app.get("/api/replay/{scenario}/at")
def replay_at(scenario: str, t: float, lookback_min: float = 120.0, radius_km: float = 75.0):
    """Fused picture at epoch second t."""
    try:
        return _replay(scenario).at(t, lookback_min=lookback_min, radius_km=radius_km)
    except KeyError:
        return JSONResponse({"error": "unknown scenario"}, status_code=404)
