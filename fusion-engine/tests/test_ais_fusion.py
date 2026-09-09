"""AIS adjudication and automatic results across live refreshes, without provider calls."""
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from fusion.fusion_ai import FusionAI, candidate_for_pair, generate_candidates, records_from_sources
from fusion.pipeline import FusionState
from fusion.store import InMemoryStore
from tests.test_fusion_ai import FakeOpenAI


def evidence():
    ts = datetime.now(timezone.utc).isoformat()
    vessel = dict(id="ais:123456789", mmsi=123456789, name="TEST VESSEL", lat=26.0, lon=56.0,
                  ts=ts, sog=5, cog=90, heading=90, nav_status=0, age_min=1)
    thermal = dict(id="firms:test", lat=26.01, lon=56.01, ts=ts, novelty=1.0, frp=12)
    return vessel, thermal


def test_ais_candidates_and_fingerprints_use_report_not_poll_age():
    vessel, thermal = evidence()
    records = records_from_sources([], [], [thermal], vessels=[vessel])
    candidate = generate_candidates(records)[0]
    assert {candidate.left.kind, candidate.right.kind} == {"ais", "firms"}
    changed_age = records_from_sources([], [], [thermal], vessels=[{**vessel, "age_min": 2}])
    assert generate_candidates(changed_age)[0].id == candidate.id
    moved = records_from_sources([], [], [thermal], vessels=[{**vessel, "lon": 56.02}])
    assert generate_candidates(moved)[0].id != candidate.id


def test_live_ais_comparison_reads_feed_and_survives_refresh():
    vessel, thermal = evidence()
    with tempfile.TemporaryDirectory() as directory:
        state = FusionState(store=InMemoryStore(), fusion_ai=FusionAI(Path(directory), FakeOpenAI()), history=[])
        state.ais_snapshot = lambda: {"vessels": [vessel]}
        state.firms = [thermal]
        result = state.adjudicate_pair("ais", vessel["id"], "firms", thermal["id"])
        assert result["left_kind"] == "ais"
        assert result["evidence"][0]["lon"] == 56.0
        vessel["lon"] = 56.02
        with patch.object(state, "_start_ai_fusion"), patch("fusion.pipeline.HISTORY_FILE", Path(directory) / "history.jsonl"):
            state.fuse()
        assert result["id"] in state.fusion_assessments
        assert state.fusion_assessments[result["id"]].evidence[0]["lon"] == 56.0
        assert any(node["kind"] == "ais" for node in state.api_graph()["nodes"])
        assert state.source_status["fusion"]["count"] == len(state.fusion_candidates)
        state.ais_snapshot = lambda: {"vessels": []}
        assert state.evidence_record("ais", vessel["id"]) is None


def test_automatic_result_is_published_before_pass_finishes_and_survives_batch_change():
    vessel, thermal = evidence()
    records = records_from_sources([], [], [thermal], vessels=[vessel])
    first = candidate_for_pair(*records)
    from dataclasses import replace
    second = candidate_for_pair(records[0], replace(records[1], id="ais:second"))
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    with tempfile.TemporaryDirectory() as directory:
        ai = FusionAI(Path(directory), FakeOpenAI())
        state = FusionState(store=InMemoryStore(), fusion_ai=ai, history=[])
        state.batch_id = "old"
        state.fusion_candidates = [first, second]
        real = ai.adjudicate
        def evaluate(candidate):
            if candidate is second:
                entered.set()
                assert release.wait(3)
                raise ValueError("one bad candidate")
            state.batch_id = "new"
            return real(candidate)
        status = state.set_source_status
        def track_status(source, phase, **kwargs):
            status(source, phase, **kwargs)
            if source == "openai" and phase in {"ready", "partial", "error"}:
                finished.set()
        with patch.object(ai, "adjudicate", side_effect=evaluate), patch.object(state, "set_source_status", side_effect=track_status):
            state._start_ai_fusion("old")
            try:
                assert entered.wait(3)
                assert len(state.fusion_assessments) == 1
                assert state.batch_id == "new"
            finally:
                release.set()
            assert finished.wait(3)
        assert len(state.fusion_assessments) == 1
        assert state.source_status["openai"]["state"] == "partial"


def test_slow_graph_writer_does_not_delay_callers_and_coalesces_pending_work():
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    calls = []
    store = Mock(name="graph")
    store.name = "neo4j"
    def persist(candidates, assessments, clusters, batch_id):
        calls.append(batch_id)
        if batch_id == "first":
            entered.set()
            assert release.wait(3)
        if batch_id == "latest":
            completed.set()
    store.record_fusion.side_effect = persist
    with tempfile.TemporaryDirectory() as directory:
        state = FusionState(store=store, fusion_ai=FusionAI(Path(directory), FakeOpenAI()), history=[])
        state._queue_fusion_artifacts(store, [], [], [], "first", "test")
        try:
            assert entered.wait(3)
            state._queue_fusion_artifacts(store, [], [], [], "obsolete", "test")
            state._queue_fusion_artifacts(store, [], [], [], "latest", "test")
        finally:
            release.set()
        assert completed.wait(3)
        assert calls == ["first", "latest"]


def test_empty_initial_pass_does_not_delay_later_candidates():
    with tempfile.TemporaryDirectory() as directory:
        state = FusionState(store=InMemoryStore(), fusion_ai=FusionAI(Path(directory), FakeOpenAI()), history=[])
        state._start_ai_fusion("empty")
        assert state._last_ai_at == 0
