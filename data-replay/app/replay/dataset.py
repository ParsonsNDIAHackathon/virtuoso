"""Generic JSONL dataset loading for replay sources."""

from __future__ import annotations

import json
from pathlib import Path

from .models import ReplayRecord


class JsonlDataset:
    def __init__(self, path: Path):
        self.path = path
        self.records = self._load(path)

    @staticmethod
    def _load(path: Path) -> list[ReplayRecord]:
        records: list[ReplayRecord] = []
        with path.open(encoding="utf-8") as stream:
            for line_number, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                    records.append(ReplayRecord.from_dict(value))
                except (json.JSONDecodeError, TypeError, ValueError) as exc:
                    raise ValueError(f"invalid replay record at {path}:{line_number}: {exc}") from exc
        records.sort(key=lambda record: record.timestamp)
        return records
