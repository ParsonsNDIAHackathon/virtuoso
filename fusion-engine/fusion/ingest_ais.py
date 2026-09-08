"""Live AIS via aisstream.io (free key, websocket). Live mode only; there is no history.

Key: AISSTREAM_API_KEY in fusion-engine/.env. Coverage depends on community receivers and is
thin inside the Persian Gulf; check `probe()` before relying on a box.

Runs a background thread that keeps the latest position per MMSI and detects AIS gaps
(vessel silent > gap_min minutes after reporting inside the box) which is the "dark ship" cue.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
URL = "wss://stream.aisstream.io/v0/stream"


def _key() -> str:
    k = os.environ.get("AISSTREAM_API_KEY")
    if not k:
        env = Path(__file__).resolve().parent.parent / ".env"
        if env.exists():
            for line in env.read_text().splitlines():
                if line.startswith("AISSTREAM_API_KEY="):
                    k = line.split("=", 1)[1].strip()
    if not k:
        raise RuntimeError("AISSTREAM_API_KEY not set")
    return k


@dataclass
class Vessel:
    id: str            # "ais:<mmsi>"
    mmsi: int
    ts: str
    lat: float
    lon: float
    name: str | None
    sog: float | None
    cog: float | None
    heading: int | None
    nav_status: int | None
    ship_type: int | None
    dark_min: float = 0.0    # minutes since last report (filled at snapshot time)

    def to_dict(self):
        return asdict(self)


class AisFeed:
    def __init__(self, boxes: list[tuple[float, float, float, float]]):
        """boxes: (lat_min, lon_min, lat_max, lon_max)."""
        self.boxes = boxes
        self.vessels: dict[int, Vessel] = {}
        self.static: dict[int, dict] = {}
        self.lock = threading.Lock()
        self.msgs = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _run(self):
        import websocket
        key = _key()
        sub = {"APIKey": key, "BoundingBoxes": [[[b[0], b[1]], [b[2], b[3]]] for b in self.boxes]}
        while not self._stop.is_set():
            try:
                ws = websocket.create_connection(URL, timeout=60)
                ws.send(json.dumps(sub))
                while not self._stop.is_set():
                    m = json.loads(ws.recv())
                    self._handle(m)
            except Exception as e:
                log.warning("aisstream reconnecting: %s", e)
                time.sleep(5)

    def _handle(self, m: dict):
        mt = m.get("MessageType")
        md = m.get("MetaData") or {}
        mmsi = md.get("MMSI")
        if mt in ("ShipStaticData", "StaticDataReport") and mmsi:
            body = m["Message"].get(mt, {})
            with self.lock:
                self.static.setdefault(mmsi, {}).update({k: v for k, v in body.items() if k in ("Name", "Type", "Destination", "CallSign")})
            return
        if mt not in ("PositionReport", "StandardClassBPositionReport") or not mmsi:
            return
        pr = m["Message"].get(mt, {})
        self.msgs += 1
        v = Vessel(
            id=f"ais:{mmsi}", mmsi=mmsi,
            ts=datetime.now(timezone.utc).isoformat(),
            lat=md.get("latitude"), lon=md.get("longitude"),
            name=(md.get("ShipName") or "").strip() or None,
            sog=pr.get("Sog"), cog=pr.get("Cog"), heading=pr.get("TrueHeading"),
            nav_status=pr.get("NavigationalStatus"), ship_type=(self.static.get(mmsi) or {}).get("Type"),
        )
        with self.lock:
            self.vessels[mmsi] = v

    def snapshot(self, max_age_min: float = 60.0, gap_min: float = 15.0) -> list[Vessel]:
        """Current picture; vessels silent longer than gap_min are flagged via dark_min."""
        now = datetime.now(timezone.utc).timestamp()
        out = []
        with self.lock:
            for v in self.vessels.values():
                age = (now - datetime.fromisoformat(v.ts).timestamp()) / 60
                if age > max_age_min:
                    continue
                v.dark_min = round(age, 1) if age >= gap_min else 0.0
                out.append(v)
        return out


def probe(boxes, seconds: int = 15) -> dict:
    """Quick coverage check: messages and distinct vessels per box."""
    import websocket
    key = _key()
    res = {}
    for b in boxes:
        ws = websocket.create_connection(URL, timeout=seconds)
        ws.send(json.dumps({"APIKey": key, "BoundingBoxes": [[[b[0], b[1]], [b[2], b[3]]]]}))
        t0, n, ids = time.time(), 0, set()
        while time.time() - t0 < seconds:
            try:
                m = json.loads(ws.recv())
            except Exception:
                break
            if m.get("MessageType") == "SubscriptionConfirmation":
                continue
            n += 1
            ids.add((m.get("MetaData") or {}).get("MMSI"))
        ws.close()
        res[b] = {"msgs": n, "vessels": len(ids)}
    return res


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(probe([(23.5, 52.0, 28.5, 59.0), (10.0, 32.0, 45.0, 75.0)]))
