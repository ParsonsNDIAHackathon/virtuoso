"""FastAPI dashboard + JSON API for the Multi-INT Fusion Engine.

    uvicorn app.server:app --reload --port 8000
    open http://localhost:8000
"""
from __future__ import annotations

import logging
import threading
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse

from fusion.pipeline import FusionState, run_loop, run_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

app = FastAPI(title="Multi-INT Fusion Engine", version="0.1")
state = FusionState()
_worker: threading.Thread | None = None


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
def events(conflict_only: bool = False, limit: int = Query(3000, le=20000)):
    return state.api_events(conflict_only, limit)


@app.get("/api/aircraft")
def aircraft(military_only: bool = False):
    return state.api_aircraft(military_only)


@app.get("/api/firms")
def firms():
    with state.lock:
        return list(state.firms)


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
