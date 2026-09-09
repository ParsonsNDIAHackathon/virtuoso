"""AOI analysis covers the circle's full snapshot and keeps citations tied to evidence."""
import importlib
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from fusion.aoi_summary import summarize_aoi
from fusion.fusion_ai import AIProviderError, AIUnavailable, FusionAI
from fusion.replay import ReplayState
from fusion.store import InMemoryStore
from tests.test_ais_api import Client, State
from tests.test_fusion_ai import record

REGION = {"id": "test", "name": "Dateline", "lat": 0, "lon": 179.9, "radius_nm": 60}
TS = "2026-08-18T10:00:00+00:00"


class Summarizer:
    available = True
    model = "test-summary"

    def __init__(self):
        self.inputs = []

    def structured(self, instructions, input_text, name, schema, **kwargs):
        body = json.loads(input_text)
        self.inputs.append(body)
        if "records" in body:
            refs = [r["record_id"] for r in body["records"]]
        else:
            refs = [ref for s in body["partial_summaries"] for f in s["findings"] for ref in f["record_ids"]]
        return {"summary": "Reported activity within the circle.",
                "findings": [{"text": "Supplied observations.", "record_ids": refs + ["invented"]}],
                "caveats": ["No explicit causal link."]}


def evidence():
    return [record(kind, kind, TS, lat=0, lon=-179.9, text="source observation")
            for kind in ("gdelt", "telegram", "adsb", "ais", "firms")]


def test_complete_circle_includes_dateline_and_excludes_box_corners(tmp_path):
    model = Summarizer()
    records = evidence() + [record("outside", "gdelt", TS, lat=.9, lon=179.0)]
    result = summarize_aoi(FusionAI(tmp_path, model), REGION, records, as_of=TS)
    assert result["total_records"] == 5
    assert all(count == 1 for count in result["counts"].values())
    assert {r["id"] for r in model.inputs[0]["records"]} == {r.id for r in evidence()}
    assert "invented" not in result["findings"][0]["record_ids"]
    assert len(result["sources"]) == 5


def test_every_record_enters_a_batch_and_reduction_preserves_provenance(tmp_path):
    model = Summarizer()
    rows = [record(f"r:{i}", "gdelt", TS, lat=0, lon=179.9, text="observation " * 30,
                   url="https://example.test/shared-article") for i in range(101)]
    result = summarize_aoi(FusionAI(tmp_path, model), REGION, rows, batch_chars=2000)
    inputs = [r for call in model.inputs for r in call.get("records", [])]
    assert len(inputs) == 101
    assert {r["id"] for r in inputs} == {r.id for r in rows}
    assert len({r["source_group"] for r in inputs}) == 1
    assert any("partial_summaries" in call for call in model.inputs)
    assert result["total_records"] == 101


def test_snapshot_cache_and_mode_and_geometry_invalidation(tmp_path):
    model = Summarizer()
    ai = FusionAI(tmp_path, model)
    first = summarize_aoi(ai, REGION, evidence(), as_of=TS)
    cached = summarize_aoi(ai, REGION, evidence(), as_of="2026-08-18T10:01:00+00:00")
    assert cached["cached"] and len(model.inputs) == 1
    assert cached["generated_at"] == first["generated_at"]
    assert cached["as_of"] != first["as_of"]
    summarize_aoi(ai, {**REGION, "radius_nm": 61}, evidence())
    summarize_aoi(ai, REGION, evidence(), mode="replay", as_of=TS)
    summarize_aoi(ai, REGION, evidence(), force=True)
    assert len(model.inputs) == 4


def test_empty_area_needs_no_key_and_expired_deadline_makes_no_provider_call(tmp_path):
    model = Summarizer()
    model.available = False
    ai = FusionAI(tmp_path, model)
    assert summarize_aoi(ai, REGION, [])["total_records"] == 0
    with pytest.raises(AIUnavailable):
        summarize_aoi(ai, REGION, evidence())
    model.available = True
    with pytest.raises(TimeoutError):
        summarize_aoi(ai, REGION, evidence(), deadline=time.monotonic() - 1)
    assert not model.inputs


@pytest.fixture
def api(monkeypatch):
    with patch("fusion.pipeline.FusionState", State):
        server = importlib.import_module("app.server")
    state = State()
    state.regions = [REGION]
    state.evidence_records = Mock(return_value=evidence())
    state.fusion_ai = Mock()
    monkeypatch.setattr(server, "state", state)
    return server, state, Client(server.app)


def test_live_and_replay_routes_use_their_own_snapshot(api):
    server, state, client = api
    replay = SimpleNamespace(t_min=1, t_max=200, evidence_records=Mock(return_value=[]), fusion_ai=Mock())
    with patch.object(server, "summarize_aoi", return_value={"summary": "done"}) as summary:
        assert client.post("/api/regions/test/analyze", json={"mode": "live"}).status_code == 200
        assert summary.call_args.args[:3] == (state.fusion_ai, REGION, evidence())
        with patch.object(server, "_replay", return_value=replay):
            response = client.post("/api/regions/test/analyze", json={"mode": "scenario", "t": 100})
        assert response.status_code == 200
        replay.evidence_records.assert_called_once_with(100)
        assert summary.call_args.args[:3] == (replay.fusion_ai, REGION, [])
        assert summary.call_args.kwargs["as_of"] == "1970-01-01T00:01:40+00:00"
    state.evidence_records.assert_called_once_with()


def test_invalid_aoi_and_missing_replay_time_do_not_call_model(api):
    server, _, client = api
    with patch.object(server, "summarize_aoi") as summary:
        assert client.post("/api/regions/gone/analyze", json={}).status_code == 404
        assert client.post("/api/regions/test/analyze", json={"mode": "scenario"}).status_code == 422
        assert client.post("/api/regions/test/analyze", json={"t": "NaN"}).status_code == 422
        summary.assert_not_called()


def test_aoi_provider_errors_and_timeout_reach_caller(api, monkeypatch):
    server, _, client = api
    for error, code in ((AIUnavailable("No API key"), 503),
                        (AIProviderError("Quota exhausted", status_code=429), 429)):
        with patch.object(server, "summarize_aoi", side_effect=error):
            assert client.post("/api/regions/test/analyze", json={}).status_code == code
    release = threading.Event()
    monkeypatch.setenv("FUSION_AOI_SUMMARY_TIMEOUT_S", "0.01")
    with patch.object(server, "summarize_aoi", side_effect=lambda *a, **kw: release.wait(1)):
        timer = threading.Timer(.1, release.set)
        timer.start()
        try:
            assert client.post("/api/regions/test/analyze", json={}).status_code == 504
        finally:
            release.set()
            timer.join()


def test_replay_evidence_uses_windows_without_live_ais(tmp_path):
    with patch("fusion.replay.DATA", tmp_path):
        replay = ReplayState("hormuz-2026-08-18", store=InMemoryStore())
    replay.loaded = True
    t = replay.t_min + 15 * 3600
    from datetime import datetime, timezone
    stamp = lambda delta: datetime.fromtimestamp(t + delta, timezone.utc).isoformat()
    # Conversion can be stubbed independently of the time-window selection.
    replay.events = [SimpleNamespace(id="current", ts=stamp(-60)), SimpleNamespace(id="old", ts=stamp(-7201)), SimpleNamespace(id="future", ts=stamp(1))]
    replay.firms = [{"id": "current", "ts": stamp(-43000)}, {"id": "old", "ts": stamp(-44000)}]
    with patch("fusion.replay.records_from_sources", return_value=[]) as convert:
        assert replay.evidence_records(t) == []
    args = convert.call_args.args
    assert [e.id for e in args[0]] == ["current"]
    assert [h["id"] for h in args[2]] == ["current"]
    assert "vessels" not in convert.call_args.kwargs
