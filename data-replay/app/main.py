"""Application entrypoint for the modular replay service."""

import os
from pathlib import Path

from fastapi import FastAPI

from .replay.api import create_router
from .replay.registry import SourceRegistry


def create_app(data_dir: Path | None = None) -> FastAPI:
    registry = SourceRegistry(data_dir or Path(os.getenv("REPLAY_DATA_DIR", "data")))
    registry.discover()
    app = FastAPI(title="Multi-source event replay")
    app.include_router(create_router(registry))

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
