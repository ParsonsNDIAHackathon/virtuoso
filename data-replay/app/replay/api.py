"""FastAPI routes for all registered replay sources."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from .registry import SourceRegistry


def create_router(registry: SourceRegistry) -> APIRouter:
    router = APIRouter()

    def controller(source: str):
        try:
            return registry.get(source)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get("/sources")
    async def sources() -> dict[str, list[str]]:
        return {"sources": sorted(registry.controllers)}

    @router.get("/status/{source}")
    async def status(source: str) -> dict[str, Any]:
        return await controller(source).status()

    @router.post("/start/{source}")
    async def start(source: str, speed: float | None = Query(default=None, gt=0)) -> dict[str, Any]:
        replay = controller(source)
        await replay.start(speed)
        return await replay.status()

    @router.post("/stop/{source}")
    async def stop(source: str) -> dict[str, Any]:
        replay = controller(source)
        await replay.stop()
        return await replay.status()

    @router.post("/reset/{source}")
    async def reset(source: str) -> dict[str, Any]:
        replay = controller(source)
        await replay.reset()
        return await replay.status()

    @router.get("/stream/{source}")
    async def stream(source: str) -> StreamingResponse:
        replay = controller(source)

        async def events():
            async for record in replay.subscribe():
                identifier = f"id: {record.event_id}\n" if record.event_id else ""
                yield f"{identifier}event: record\ndata: {json.dumps(record.to_dict())}\n\n"

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    return router
