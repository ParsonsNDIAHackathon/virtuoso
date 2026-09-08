"""Data structures shared by every replay source."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def parse_timestamp(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        timestamp = value
    else:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return timestamp.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class ReplayRecord:
    timestamp: datetime
    payload: dict[str, Any]
    event_id: str | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ReplayRecord":
        if "timestamp" not in value or "payload" not in value:
            raise ValueError("a replay record requires timestamp and payload")
        if not isinstance(value["payload"], dict):
            raise ValueError("replay payload must be an object")
        return cls(
            timestamp=parse_timestamp(value["timestamp"]),
            payload=value["payload"],
            event_id=str(value["event_id"]) if value.get("event_id") is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "payload": self.payload,
        }
        if self.event_id is not None:
            result["event_id"] = self.event_id
        return result
