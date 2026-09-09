"""Mission configuration: the knowledge that belongs to a theater, kept out of the engine.

Built-in values are the Hormuz instance. A JSON file named by FUSION_MISSION_CONFIG, or
<FUSION_DATA_DIR>/config/mission.json, overrides or extends them. A second mission file is
the portability test: nothing in the engine should need editing to run elsewhere.

Keys
  dateline_places      place-name prefixes that GDELT uses as outlet datelines, not incident
                       sites. Events there still count in the news series; they are not paired
                       with nearby aircraft or thermal detections as if the story happened there.
  expected_cooccurrence cells ([lat, lon] south-west corner, 1-degree) where two streams always
                       fire together (an airport next to a capital's newsroom). Departures there
                       need every stream to move, not just proximity.
  z_threshold          departure threshold on the robust z-score.
  persistent_bins      consecutive departed bins before a departure is a persistent condition.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULTS: dict = {
    "id": "hormuz",
    "dateline_places": ["Dubai", "Abu Dhabi", "Tehran", "Washington", "Doha", "Muscat", "Riyadh",
                        "Manama", "Kuwait", "Gulf News", "Ministry Of", "Al-Awir"],
    "expected_cooccurrence": [[25, 55], [24, 54]],   # DXB / AUH terminal areas
    "z_threshold": 2.0,
    "persistent_bins": 3,
}


def load() -> dict:
    path = os.getenv("FUSION_MISSION_CONFIG") or str(
        Path(os.getenv("FUSION_DATA_DIR") or Path(__file__).resolve().parent.parent / "data") / "config" / "mission.json")
    cfg = dict(DEFAULTS)
    try:
        override = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return cfg
    except Exception as e:
        log.warning("mission config %s ignored: %s", path, e)
        return cfg
    for key, value in override.items():
        if key in ("dateline_places",) and isinstance(value, list):
            cfg[key] = list(dict.fromkeys(list(cfg[key]) + [str(v) for v in value]))
        elif key == "expected_cooccurrence" and isinstance(value, list):
            cfg[key] = [list(c) for c in cfg[key]] + [list(c) for c in value]
        else:
            cfg[key] = value
    log.info("mission config loaded from %s (%s)", path, cfg.get("id"))
    return cfg


def is_dateline(place: str | None, cfg: dict | None = None) -> bool:
    if not place:
        return False
    cfg = cfg or CONFIG
    p = place.strip().lower()
    return any(p.startswith(name.lower()) for name in cfg["dateline_places"])


def is_expected_cooccurrence(cell: tuple[int, int], cfg: dict | None = None) -> bool:
    cfg = cfg or CONFIG
    return [cell[0], cell[1]] in cfg["expected_cooccurrence"]


CONFIG = load()
