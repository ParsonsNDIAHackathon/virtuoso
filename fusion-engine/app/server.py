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
from fusion.fusion_ai import AIProviderError, AIUnavailable

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


def _tails_in_view(tails: list[dict], bbox: str | None, limit: int) -> list[dict]:
    """Keep paths whose current (last) position is in the requested map view."""
    bounds = _parse_bbox(bbox)
    if bounds is None:
        return tails[:limit]
    west, south, east, north = bounds

    def contains(tail: dict) -> bool:
        if not tail.get("coords"):
            return False
        lat, lon = tail["coords"][-1]
        return south <= lat <= north and (west <= lon <= east if west <= east else lon >= west or lon <= east)

    return [tail for tail in tails if contains(tail)][:limit]


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


@app.get("/api/navint")
def navint():
    """Navigation-integrity degradation per 1-degree cell from the live ADS-B snapshot.
    Each cell reports the number of aircraft that transmit integrity fields; cells below the
    minimum are labeled insufficient rather than omitted."""
    return state.api_navint()


@app.get("/api/aircraft/tails")
def aircraft_tails(minutes: int = Query(30, ge=2, le=120), limit: int = Query(3000, ge=1, le=20000), bbox: str | None = None):
    return _tails_in_view(state.api_tails(minutes), bbox, limit)


@app.get("/api/preview")
def link_preview(url: str = Query(..., min_length=8, max_length=2048)):
    """Open Graph / meta summary of a source page (cached). Used for the Inspector's source card
    because most publishers forbid iframes."""
    from fusion.preview import preview
    return preview(url)


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


@app.get("/api/fusion/status")
def fusion_ai_status():
    return {
        "provider": "openai", "model": state.fusion_ai.client.model,
        "configured": state.fusion_ai.available,
        "prompt_version": __import__("fusion.fusion_ai", fromlist=["PROMPT_VERSION"]).PROMPT_VERSION,
    }


@app.get("/api/fusion/candidates")
def fusion_candidates(limit: int = Query(300, ge=1, le=2000)):
    return state.api_fusion_candidates(limit)


@app.get("/api/fusion/assessments")
def fusion_assessments(include_rejected: bool = False, limit: int = Query(300, ge=1, le=2000)):
    return state.api_fusion_assessments(include_rejected, limit)


@app.get("/api/fusion/clusters")
def fusion_clusters(limit: int = Query(100, ge=1, le=500)):
    return state.api_fusion_clusters(limit)


def _social_platform(rec: dict) -> str:
    from fusion.ingest_social import platform_of
    return platform_of(rec) or "unknown"


@app.get("/api/social")
def social(platform: str | None = None, limit: int = Query(500, ge=1, le=5000), bbox: str | None = None):
    """Recent geolocated social posts (all platforms) that feed the correlator.

    `platform` optionally filters to telegram|reddit|bluesky|mastodon.
    """
    with state.lock:
        recs = [e.to_dict() for e in list(state.social)]
    if platform:
        want = platform.strip().lower()
        recs = [r for r in recs if _social_platform(r) == want]
    # Newest first; _in_view preserves order and applies the map viewport.
    recs.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return _in_view(recs, bbox, limit)


@app.get("/api/social/platforms")
def social_platforms():
    """Configured social platforms, their targets, and live counts."""
    from fusion.ingest_social import PLATFORM_LABELS, default_targets, enabled_platforms
    with state.lock:
        recs = [e.to_dict() for e in list(state.social)]
        sources = {k: dict(v) for k, v in state.source_status.items()}
    counts: dict[str, int] = {}
    for r in recs:
        p = _social_platform(r)
        counts[p] = counts.get(p, 0) + 1
    plats = enabled_platforms()
    return {
        "platforms": [
            {"id": p, "label": PLATFORM_LABELS.get(p, p),
             "targets": default_targets(p), "count": counts.get(p, 0),
             "status": sources.get(f"social:{p}", sources.get(p, {})).get("state")}
            for p in plats
        ],
        "total": len(recs),
    }


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


@app.get("/api/source/preview")
def source_preview(url: str = Query(..., min_length=8, max_length=2000)):
    """Same-origin article summary for the source viewer.

    Browsers refuse to iframe most news sites (X-Frame-Options /
    frame-ancestors), so the engine fetches the page server-side and returns
    its title, description, lead image and article text for inline rendering.
    Invalid URLs yield 422; unreachable pages yield a 200 error payload so the
    viewer can fall back to the engine record plus an external link.
    """
    from fusion.source_preview import PreviewError, preview_url
    try:
        return preview_url(url)
    except PreviewError as e:
        raise HTTPException(422, str(e)) from None


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


class EvidenceRef(BaseModel):
    kind: str
    id: str


class AdjudicationIn(BaseModel):
    left: EvidenceRef
    right: EvidenceRef
    mode: str = "live"
    t: float | None = None
    force: bool = False


@app.post("/api/fusion/adjudicate")
def adjudicate(body: AdjudicationIn):
    if body.left.id == body.right.id and body.left.kind == body.right.kind:
        raise HTTPException(422, "select two different records")
    try:
        if body.mode == "live":
            return state.adjudicate_pair(body.left.kind, body.left.id, body.right.kind, body.right.id, force=body.force)
        if body.t is None:
            raise HTTPException(422, "replay adjudication requires t")
        return _replay(body.mode).adjudicate_pair(
            body.t, body.left.kind, body.left.id, body.right.kind, body.right.id,
            force=body.force,
        )
    except AIUnavailable as error:
        raise HTTPException(503, str(error)) from None
    except AIProviderError as error:
        # A provider quota/rate limit is actionable for the caller. Other upstream failures remain
        # service errors rather than masquerading as failures of this API route.
        status = 429 if error.status_code == 429 else 503 if error.status_code >= 500 else 502
        code = f" ({error.code})" if error.code else ""
        raise HTTPException(status, f"OpenAI API error{code}: {error}") from None
    except KeyError as error:
        raise HTTPException(404, str(error)) from None
    except HTTPException:
        raise
    except Exception as error:
        log.exception("adjudication failed")
        raise HTTPException(502, f"OpenAI adjudication failed: {str(error)[:180]}") from None


@app.get("/api/regions")
def regions():
    with state.lock:
        return list(state.regions)


@app.post("/api/regions")
def add_region(r: RegionIn):
    return state.add_region(r.lat, r.lon, r.radius_nm, r.name)


class RegionRename(BaseModel):
    name: str


@app.patch("/api/regions/{rid}")
def rename_region(rid: str, body: RegionRename):
    r = state.rename_region(rid, body.name)
    if not r:
        return JSONResponse({"error": "unknown region"}, status_code=404)
    return r


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


@app.get("/api/replay/{scenario}/evidence")
def replay_evidence(scenario: str):
    """Analyst-reviewed (curated, manual) records for a scenario: vessels, sources, claims, leads.
    Not sensor data; never enters correlation or alerts."""
    try:
        b = _replay(scenario).evidence()
    except KeyError:
        return JSONResponse({"error": "unknown scenario"}, status_code=404)
    if b is None:
        return JSONResponse({"error": "no curated evidence for this scenario"}, status_code=404)
    return b


@app.get("/api/replay/{scenario}/at")
def replay_at(scenario: str, t: float, lookback_min: float = 120.0, radius_km: float = 75.0):
    """Fused picture at epoch second t."""
    try:
        return _replay(scenario).at(t, lookback_min=lookback_min, radius_km=radius_km)
    except KeyError:
        return JSONResponse({"error": "unknown scenario"}, status_code=404)
