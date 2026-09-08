"""Shared correlation rules used when facts are stored and matched in Neo4j."""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass

from .ingest_adsb import AirTrack
from .ingest_gdelt import OsintEvent

CENTROID_TYPES = {1, 2, 5}
_STOP = {"THE", "OF", "AND", "GOVERNMENT", "STATE", "REPUBLIC", "PRESIDENT", "MINISTER"}


def resolve_actor(name: str | None) -> str | None:
    """Normalize actor strings so equivalent names use one graph identity."""
    if not name:
        return None
    normalized = name.upper().strip()
    normalized = re.sub(r"[^A-Z0-9 ]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    aliases = {
        "US": "UNITED STATES", "USA": "UNITED STATES", "U S": "UNITED STATES", "AMERICA": "UNITED STATES",
        "UK": "UNITED KINGDOM", "BRITAIN": "UNITED KINGDOM", "GREAT BRITAIN": "UNITED KINGDOM",
        "RUSSIAN": "RUSSIA", "RUSSIAN FEDERATION": "RUSSIA", "MOSCOW": "RUSSIA",
        "UKRAINIAN": "UKRAINE", "KYIV": "UKRAINE", "KIEV": "UKRAINE",
        "CHINESE": "CHINA", "BEIJING": "CHINA", "PRC": "CHINA",
        "ISRAELI": "ISRAEL", "IRANIAN": "IRAN", "TEHRAN": "IRAN",
    }
    normalized = aliases.get(normalized, normalized)
    return None if not normalized or normalized in _STOP else normalized


def resolve_location(lat: float, lon: float, name: str) -> str:
    return f"loc:{round(lat, 1)},{round(lon, 1)}"


def event_severity(event: OsintEvent) -> float:
    """Return the 0..1 event component of the correlation score."""
    severity = 0.15
    if event.is_conflict:
        severity += 0.35
    severity += max(0.0, -event.goldstein) / 10.0 * 0.3
    severity += max(0.0, -event.tone) / 15.0 * 0.1
    severity += min(event.num_mentions, 50) / 50.0 * 0.1
    return min(severity, 1.0)


def aircraft_weight(track: AirTrack) -> float:
    """Return the 0..1 aircraft component of the correlation score."""
    weight = 0.4
    if track.military:
        weight += 0.4
    if track.alt_ft is not None and track.alt_ft < 10000:
        weight += 0.1
    if track.emergency or track.squawk in ("7500", "7600", "7700"):
        weight += 0.2
    return min(weight, 1.0)


@dataclass
class Alert:
    id: str
    score: float
    event_id: str
    aircraft_id: str
    distance_km: float
    dt_min: float
    event_label: str
    place: str
    aircraft_label: str
    lat: float
    lon: float
    reason: str

    def to_dict(self):
        return asdict(self)


def dedupe_alerts(alerts: list[Alert]) -> list[Alert]:
    """Collapse corroborating reports at one rounded location for each aircraft."""
    best: dict[tuple, Alert] = {}
    count: dict[tuple, int] = defaultdict(int)
    for alert in alerts:
        key = (alert.aircraft_id, round(alert.lat, 1), round(alert.lon, 1))
        count[key] += 1
        if key not in best or alert.score > best[key].score:
            best[key] = alert
    output = []
    for key, alert in best.items():
        reports = count[key]
        if reports > 1:
            alert.score = round(min(1.0, alert.score + 0.05 * min(reports - 1, 3)), 3)
            alert.reason += f"; {reports} corroborating OSINT reports at this location"
        output.append(alert)
    return output
