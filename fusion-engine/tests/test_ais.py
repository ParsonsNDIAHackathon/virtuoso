"""AIS geography, parsing, freshness, and subscription lifecycle without a live provider."""
import json
import math
import threading
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from types import SimpleNamespace

from fusion.ingest_ais import AisFeed, aoi_boxes, inside
from fusion.geo import EARTH_KM


def report(lat=0, lon=0, mmsi=123456789, **fields):
    return {"MessageType": "PositionReport", "MetaData": {"MMSI": mmsi, "ShipName": " SAMPLE "},
            "Message": {"PositionReport": {"Latitude": lat, "Longitude": lon, "Sog": 8, "Cog": 90, "TrueHeading": 91, **fields}}}


class AisTests(unittest.TestCase):
    def setUp(self):
        self.feed = AisFeed()
        self.feed.configure([dict(lat=0, lon=0, radius_nm=60)])

    def test_circle_rejects_box_corner_and_departing_vessel(self):
        self.feed._handle(report())
        self.assertEqual(len(self.feed.snapshot()["vessels"]), 1)
        self.assertFalse(inside(.9, .9, self.feed.regions))
        self.feed._handle(report(.9, .9))
        self.assertEqual(self.feed.snapshot()["vessels"], [])

    def test_metadata_cases_class_b_and_sentinels(self):
        for kind in ("PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport"):
            for latkey, lonkey in (("latitude", "longitude"), ("Latitude", "Longitude")):
                msg = {"MessageType": kind, "MetaData": {"MMSI": 123456789, latkey: 0, lonkey: 0},
                       "Message": {kind: {"Sog": 102.3, "Cog": 360, "TrueHeading": 511}}}
                self.feed._handle(msg)
                v = self.feed.snapshot()["vessels"][0]
                self.assertIsNone(v["sog"])
                self.assertIsNone(v["cog"])
                self.assertIsNone(v["heading"])
        self.feed._handle(report(91, 181, mmsi=222222222))
        self.feed._handle(report(mmsi=222222222, Valid=False))
        self.assertEqual(len(self.feed.snapshot()["vessels"]), 1)

    def test_reconfiguration_filters_immediately_and_no_aoi_is_empty(self):
        self.feed._handle(report())
        self.feed.configure([dict(lat=10, lon=10, radius_nm=60)])
        self.assertEqual(self.feed.snapshot()["vessels"], [])
        self.feed.configure([])
        self.feed._handle(report())
        self.assertEqual(self.feed.snapshot()["vessels"], [])
        self.assertEqual(aoi_boxes([]), [])

    def test_freshness_and_out_of_order(self):
        msg = report()
        msg["MetaData"]["time_utc"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
        self.feed._handle(msg)
        self.assertGreaterEqual(self.feed.snapshot()["vessels"][0]["age_min"], 20)
        self.feed._handle(report(lon=.1))
        self.feed._handle(msg)
        self.assertEqual(self.feed.snapshot()["vessels"][0]["lon"], .1)
        self.feed.vessels[123456789]["ts"] = (datetime.now(timezone.utc) - timedelta(minutes=61)).isoformat()
        self.assertEqual(self.feed.snapshot()["vessels"], [])
        msg["MetaData"]["time_utc"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        self.feed._handle(msg)
        self.assertEqual(self.feed.snapshot()["vessels"], [])

    def test_bounding_boxes_cover_circle_perimeter_including_dateline_and_poles(self):
        for lat, lon in ((26.55, 56.45), (0, 179.8), (0, -179.8), (89, 10), (-89, 10)):
            boxes = aoi_boxes([dict(lat=lat, lon=lon, radius_nm=250)])
            angular = 250 * 1.852 / EARTH_KM
            p, l = math.radians(lat), math.radians(lon)
            for bearing in range(0, 360, 5):
                b = math.radians(bearing)
                p2 = math.asin(math.sin(p)*math.cos(angular)+math.cos(p)*math.sin(angular)*math.cos(b))
                l2 = l + math.atan2(math.sin(b)*math.sin(angular)*math.cos(p), math.cos(angular)-math.sin(p)*math.sin(p2))
                la, lo = math.degrees(p2), (math.degrees(l2)+180)%360-180
                self.assertTrue(any(s-1e-8 <= la <= n+1e-8 and w-1e-8 <= lo <= e+1e-8 for ((s,w),(n,e)) in boxes))

    def test_missing_key_and_no_aoi_never_connect(self):
        for regions, key in (([], "test"), ([dict(lat=0, lon=0, radius_nm=60)], "")):
            feed = AisFeed()
            feed.configure(regions)
            with patch.dict('os.environ', {'AISSTREAM_API_KEY': key}), patch.dict('sys.modules', {'websocket': None}):
                # End the loop at its first idle wait; no socket library should be imported.
                feed._wake.wait = lambda _: feed._stop.set()
                feed._run()
            self.assertEqual(feed.state, "partial")

    def test_subscription_scope_and_socket_cleanup(self):
        feed = self.feed
        sent = []
        class Socket:
            closed = False
            def send(self, value): sent.append(json.loads(value))
            def recv(self):
                feed._stop.set()
                return json.dumps(report())
            def close(self): self.closed = True
        socket = Socket()
        module = SimpleNamespace(create_connection=lambda *a, **kw: socket, WebSocketTimeoutException=TimeoutError)
        with patch.dict('os.environ', {'AISSTREAM_API_KEY': 'test'}), patch.dict('sys.modules', {'websocket': module}):
            feed._wake.wait = lambda _: None
            feed._run()
        self.assertEqual(sent[0]['BoundingBoxes'], aoi_boxes(feed.regions))
        self.assertIn('StandardClassBPositionReport', sent[0]['FilterMessageTypes'])
        self.assertTrue(socket.closed)
        self.assertNotIn('test', str(feed.snapshot()))

if __name__ == '__main__':
    unittest.main()
