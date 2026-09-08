"""FastAPI dashboard + JSON API for the Multi-INT Fusion Engine.

    uvicorn app.server:app --reload --port 8000
    open http://localhost:8000
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from fusion.pipeline import FusionState, run_loop, run_once

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Multi-INT Fusion Engine", version="0.1")
state = FusionState()
_worker: threading.Thread | None = None


@app.on_event("startup")
def _startup():
    global _worker
    # First fuse synchronously so the dashboard has data, then keep streaming in the background.
    try:
        run_once(state, windows=2)
    except Exception as e:
        log.exception("initial fuse failed: %s", e)
    _worker = threading.Thread(target=run_loop, args=(state,), kwargs={"windows": 2}, daemon=True)
    _worker.start()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/status")
def status():
    snap = state.snapshot()
    return {"updated": snap["updated"], "gdelt_window": snap["gdelt_window"], "counts": snap["counts"]}


@app.get("/api/alerts")
def alerts(limit: int = Query(100, le=2000), min_score: float = 0.0):
    with state.lock:
        out = [a.to_dict() for a in state.alerts if a.score >= min_score][:limit]
    return out


@app.get("/api/events")
def events(conflict_only: bool = False, limit: int = Query(3000, le=20000)):
    with state.lock:
        ev = [e.to_dict() for e in state.events + state.social if (e.is_conflict or not conflict_only)]
    return ev[:limit]


@app.get("/api/aircraft")
def aircraft(military_only: bool = False):
    with state.lock:
        return [t.to_dict() for t in state.tracks if (t.military or not military_only)]


@app.get("/api/graph")
def graph():
    with state.lock:
        return state.graph or {"nodes": [], "links": [], "stats": {}}


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
    """Neighbourhood of one graph node (for click-through in the UI)."""
    with state.lock:
        g = state.graph
    nodes = {n["id"]: n for n in g.get("nodes", [])}
    if node_id not in nodes:
        return JSONResponse({"error": "unknown node"}, status_code=404)
    links = [l for l in g.get("links", []) if node_id in (l["source"], l["target"])]
    nbr = {l["source"] if l["target"] == node_id else l["target"] for l in links}
    return {"node": nodes[node_id], "links": links, "neighbors": [nodes[n] for n in nbr if n in nodes]}


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


app.mount("/static", StaticFiles(directory=STATIC), name="static")
