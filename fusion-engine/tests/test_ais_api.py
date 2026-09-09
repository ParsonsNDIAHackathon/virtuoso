"""Exercise AIS HTTP routes with in-memory AOIs and no provider connection."""
import asyncio
import json
import importlib
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from fusion.ingest_ais import AisFeed
from tests.test_ais import report

class Client:
    def __init__(self, app):
        self.app = app

    def request(self, method, url, body=None):
        async def run():
            messages = []
            path, _, query = url.partition('?')
            async def receive():
                return {"type": "http.request", "body": json.dumps(body).encode() if body is not None else b"", "more_body": False}
            async def send(message):
                messages.append(message)
            await self.app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method,
                "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": query.encode(),
                "headers": [(b"content-type", b"application/json")], "server": ("test", 80), "client": ("test", 123)}, receive, send)
            status = next(m['status'] for m in messages if m['type'] == 'http.response.start')
            payload = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
            return SimpleNamespace(status_code=status, json=lambda: json.loads(payload))
        return asyncio.run(run())

    def get(self, url): return self.request('GET', url)
    def post(self, url, json): return self.request('POST', url, json)
    def delete(self, url): return self.request('DELETE', url)

class State:
    def __init__(self):
        self.lock = threading.RLock()
        self.regions = []

    def add_region(self, lat, lon, radius_nm, name):
        r = dict(id=str(len(self.regions)), lat=lat, lon=lon, radius_nm=min(250, radius_nm), name=name)
        self.regions.append(r)
        return r

    def remove_region(self, rid):
        old = len(self.regions)
        self.regions = [r for r in self.regions if r['id'] != rid]
        return len(self.regions) != old

class AisApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch('fusion.pipeline.FusionState', State):
            cls.server = importlib.import_module('app.server')

    def setUp(self):
        self.server.state = State()
        self.server.ais = AisFeed()
        self.client = Client(self.server.app)

    def test_aoi_create_delete_and_api_scope(self):
        self.assertEqual(self.client.get('/api/ais').json()['vessels'], [])
        region = self.client.post('/api/regions', json={'lat': 0, 'lon': 0, 'radius_nm': 60}).json()
        self.server.ais._handle(report())
        self.server.ais._handle(report(.9, .9, mmsi=222222222))
        data = self.client.get('/api/ais?bbox=-180,-90,180,90').json()
        self.assertEqual([v['mmsi'] for v in data['vessels']], [123456789])
        self.assertEqual(self.client.delete('/api/regions/'+region['id']).status_code, 200)
        self.assertEqual(self.client.get('/api/ais').json()['vessels'], [])
        self.assertEqual(self.server.ais.regions, [])

    def test_invalid_geography_is_rejected(self):
        for body in ({'lat': 91, 'lon': 0}, {'lat': 0, 'lon': 181}, {'lat': 0, 'lon': 0, 'radius_nm': -5}):
            self.assertEqual(self.client.post('/api/regions', json=body).status_code, 422)
        self.assertEqual(self.server.ais.regions, [])

if __name__ == '__main__':
    unittest.main()
