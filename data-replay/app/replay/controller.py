"""Pause/resume/replay mechanics independent of any data source."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

from .dataset import JsonlDataset
from .models import ReplayRecord


class ReplayController:
    def __init__(self, dataset: JsonlDataset, speed: float = 1.0):
        if speed <= 0:
            raise ValueError("speed must be greater than zero")
        self.dataset = dataset
        self.speed = speed
        self.cursor = 0
        self.playing = False
        self._lock = asyncio.Lock()
        self._runner: asyncio.Task[None] | None = None
        self._subscribers: set[asyncio.Queue[ReplayRecord]] = set()

    @property
    def complete(self) -> bool:
        return self.cursor >= len(self.dataset.records)

    async def start(self, speed: float | None = None) -> None:
        if speed is not None:
            if speed <= 0:
                raise ValueError("speed must be greater than zero")
            self.speed = speed
        async with self._lock:
            if self.complete:
                return
            self.playing = True
            if self._runner is None or self._runner.done():
                self._runner = asyncio.create_task(self._run())

    async def stop(self) -> None:
        async with self._lock:
            self.playing = False

    async def reset(self) -> None:
        async with self._lock:
            self.playing = False
            self.cursor = 0

    async def status(self) -> dict[str, Any]:
        async with self._lock:
            return {
                "playing": self.playing,
                "cursor": self.cursor,
                "total": len(self.dataset.records),
                "speed": self.speed,
                "complete": self.complete,
            }

    async def subscribe(self) -> AsyncIterator[ReplayRecord]:
        queue: asyncio.Queue[ReplayRecord] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    async def _run(self) -> None:
        remaining_delay = 0.0
        previous_timestamp = None
        while True:
            async with self._lock:
                if not self.playing or self.complete:
                    if self.complete:
                        self.playing = False
                    return
                record = self.dataset.records[self.cursor]
                speed = self.speed

            if previous_timestamp is not None and remaining_delay == 0:
                remaining_delay = max(
                    0.0,
                    (record.timestamp - previous_timestamp).total_seconds() / speed,
                )
            while remaining_delay > 0:
                started = time.monotonic()
                await asyncio.sleep(min(remaining_delay, 0.05))
                elapsed = time.monotonic() - started
                async with self._lock:
                    if not self.playing:
                        return
                remaining_delay = max(0.0, remaining_delay - elapsed)

            async with self._lock:
                if not self.playing:
                    return
                self.cursor += 1
            previous_timestamp = record.timestamp
            for queue in tuple(self._subscribers):
                await queue.put(record)
