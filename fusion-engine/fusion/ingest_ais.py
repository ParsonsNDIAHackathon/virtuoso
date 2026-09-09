"""Live AIS positions, subscribed by AOI bounding boxes and filtered to AOI circles."""
from __future__ import annotations

import json
import math
import os
import random
import threading
from datetime import datetime, timezone

from .geo import EARTH_KM, haversine_km

URL = "wss://stream.aisstream.io/v0/stream"
POSITION_TYPES = ("PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport")


def aoi_boxes(regions):
    """Spherical circle bounds in lat/lon order, split at the antimeridian."""
    boxes = []
    for r in regions:
        lat, lon = r["lat"], r["lon"]
        angle = r["radius_nm"] * 1.852 / EARTH_KM
        delta = math.degrees(angle)
        south, north = max(-90, lat - delta), min(90, lat + delta)
        if south == -90 or north == 90:
            boxes.append([[south, -180], [north, 180]])
            continue
        dx = math.degrees(math.asin(min(1, math.sin(angle) / math.cos(math.radians(lat)))))
        west, east = lon - dx, lon + dx
        if west < -180:
            boxes.extend([[[south, west + 360], [north, 180]], [[south, -180], [north, east]]])
        elif east > 180:
            boxes.extend([[[south, west], [north, 180]], [[south, -180], [north, east - 360]]])
        else:
            boxes.append([[south, west], [north, east]])
    return boxes


def inside(lat, lon, regions):
    return any(haversine_km(lat, lon, r["lat"], r["lon"]) <= r["radius_nm"] * 1.852 for r in regions)


def number(value, low, high):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high:
        return value
    return None


class AisFeed:
    def __init__(self):
        self.lock = threading.RLock()
        self.regions = []
        self.vessels = {}
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._ws = None
        self._generation = 0
        self.state = "starting"
        self.detail = "Waiting for AIS subscription."
        self.updated = None

    def configure(self, regions):
        circles = sorted([dict(lat=r["lat"], lon=r["lon"], radius_nm=r.get("radius_nm", 100)) for r in regions], key=lambda r: (r["lat"], r["lon"], r["radius_nm"]))
        with self.lock:
            if circles == self.regions:
                return
            self.regions = circles
            self._generation += 1
            self.vessels = {k: v for k, v in self.vessels.items() if inside(v["lat"], v["lon"], circles)}
            self.state, self.detail = "starting", "Updating AIS areas of interest."
            ws = self._ws
        self._wake.set()
        if ws:
            ws.close()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="ais-feed")
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        with self.lock:
            ws = self._ws
        if ws:
            ws.close()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        backoff = 2
        while not self._stop.is_set():
            self._wake.clear()
            with self.lock:
                boxes = aoi_boxes(self.regions)
                generation = self._generation
            key = os.getenv("AISSTREAM_API_KEY", "").strip()
            if not boxes or not key:
                with self.lock:
                    self.state = "partial"
                    self.detail = "Add an area of interest to receive AIS vessels." if not boxes else "AISSTREAM_API_KEY is not configured on the server."
                self._wake.wait(5)
                continue
            ws = None
            try:
                import websocket
                with self.lock:
                    self.state, self.detail = "starting", "Connecting to AIS for AOIs."
                ws = websocket.create_connection(URL, timeout=10)
                with self.lock:
                    self._ws = ws
                    if generation != self._generation or self._stop.is_set():
                        continue
                ws.send(json.dumps({"APIKey": key, "BoundingBoxes": boxes, "FilterMessageTypes": list(POSITION_TYPES)}))
                while not self._stop.is_set():
                    with self.lock:
                        if generation != self._generation:
                            break
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        ws.ping()
                        continue
                    if not raw:
                        raise ConnectionError("closed")
                    message = json.loads(raw)
                    if message.get("error") or message.get("Error") or message.get("MessageType") == "Error":
                        raise ConnectionError("subscription rejected")
                    with self.lock:
                        if generation != self._generation:
                            break
                        self.state = "ready"
                        self.detail = "Connected · AOI circles only. No vessels can mean no receiver coverage."
                        self._handle(message)
                    backoff = 2
            except Exception:
                # Never include provider responses or exception strings that could contain credentials.
                with self.lock:
                    self.state, self.detail = "error", "AIS connection failed; retrying. Check server credentials and connectivity."
            finally:
                if ws:
                    ws.close()
                with self.lock:
                    self._ws = None
            self._wake.wait(backoff + random.random())
            backoff = min(60, backoff * 2)

    def _handle(self, message):
        kind = message.get("MessageType")
        if kind not in POSITION_TYPES:
            return
        body = (message.get("Message") or {}).get(kind) or {}
        meta = message.get("MetaData") or {}
        if body.get("Valid") is False:
            return
        mmsi = meta.get("MMSI", body.get("UserID"))
        if not isinstance(mmsi, int) or not 100000000 <= mmsi <= 999999999:
            return
        lat = number(body.get("Latitude", meta.get("Latitude", meta.get("latitude"))), -90, 90)
        lon = number(body.get("Longitude", meta.get("Longitude", meta.get("longitude"))), -180, 180)
        if lat is None or lon is None:
            return
        with self.lock:
            if not inside(lat, lon, self.regions):
                self.vessels.pop(mmsi, None)
                return
            now = datetime.now(timezone.utc)
            # Provider receipt time, when supplied, prevents old reports masquerading as current.
            try:
                stamp = datetime.fromisoformat(str(meta.get("time_utc", "")).replace(" UTC", "+00:00").replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
            except ValueError:
                stamp = now
            if (now - stamp).total_seconds() > 3600 or (stamp - now).total_seconds() > 60:
                return
            previous = self.vessels.get(mmsi)
            if previous and datetime.fromisoformat(previous["ts"]) > stamp:
                return
            name = meta.get("ShipName")
            self.vessels[mmsi] = dict(id=f"ais:{mmsi}", mmsi=mmsi, ts=stamp.isoformat(), lat=lat, lon=lon,
                name=name.strip() if isinstance(name, str) else None,
                sog=number(body.get("Sog"), 0, 102.2), cog=number(body.get("Cog"), 0, 359.9),
                heading=number(body.get("TrueHeading"), 0, 359), nav_status=number(body.get("NavigationalStatus"), 0, 14))
            self.updated = now.isoformat()
            self._prune(now)

    def _prune(self, now):
        self.vessels = {k: v for k, v in self.vessels.items() if (now - datetime.fromisoformat(v["ts"])).total_seconds() <= 3600}

    def snapshot(self):
        with self.lock:
            now = datetime.now(timezone.utc)
            self._prune(now)
            vessels = [dict(v, age_min=round((now - datetime.fromisoformat(v["ts"])).total_seconds() / 60, 1)) for v in self.vessels.values() if inside(v["lat"], v["lon"], self.regions)]
            detail = self.detail if self.regions else "Add an area of interest to receive AIS vessels."
            return {"vessels": vessels, "source": {"state": self.state if self.regions else "partial", "label": "AIS vessels", "detail": detail, "updated": self.updated, "count": len(vessels)}}
