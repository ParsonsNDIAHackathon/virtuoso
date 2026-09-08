"""Registration and discovery of replayable data sources."""

from __future__ import annotations

from pathlib import Path
import re

from .controller import ReplayController
from .dataset import JsonlDataset

SOURCE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


class SourceRegistry:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.controllers: dict[str, ReplayController] = {}

    def discover(self) -> None:
        if not self.data_dir.exists():
            return
        for source_dir in sorted(self.data_dir.iterdir()):
            if not source_dir.is_dir() or not SOURCE_NAME.fullmatch(source_dir.name):
                continue
            data_path = source_dir / "events.jsonl"
            if data_path.exists():
                self.register(source_dir.name, JsonlDataset(data_path))

    def register(self, name: str, dataset: JsonlDataset, speed: float = 1.0) -> None:
        if not SOURCE_NAME.fullmatch(name):
            raise ValueError(f"invalid source name: {name}")
        self.controllers[name] = ReplayController(dataset, speed=speed)

    def get(self, name: str) -> ReplayController:
        try:
            return self.controllers[name]
        except KeyError as exc:
            raise KeyError(f"unknown source: {name}") from exc
