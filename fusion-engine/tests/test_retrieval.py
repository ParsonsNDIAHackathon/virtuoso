"""Retrieval prioritizes source facts while limiting duplicate and weak model calls."""
import threading
from dataclasses import replace
from unittest.mock import patch

from fusion.fusion_ai import FusionAI, candidate_for_pair, generate_candidates
from fusion.pipeline import FusionState
from fusion.retrieval import coverage_key, refresh_signature, select_batch
from fusion.store import InMemoryStore
from tests.test_fusion_ai import FakeOpenAI, record

TS = "2026-08-18T10:00:00+00:00"


def test_reported_mmsi_beats_nearby_unnamed_vessels_and_respects_time():
    report = record("news:1", "gdelt", TS, text="Tanker MMSI 211123456 reported a fire.")
    named = record("vessel:1", "ais", TS, lat=30, lon=60, mmsi="211123456")
    near = record("vessel:2", "ais", TS, mmsi="311123456")
    stale = replace(named, id="vessel:old", ts="2026-08-16T10:00:00+00:00")
    candidates = generate_candidates([report, near, named, stale])
    assert candidates[0].right.id == named.id
    assert candidates[0].match_type == "identifier"
    assert candidates[0].distance_km > 100
    assert all(c.right.id != stale.id for c in candidates)


def test_complete_names_and_identifier_boundaries():
    vessel = record("vessel:1", "ais", TS, name="MV Minoan Dignity", mmsi="211123456")
    assert candidate_for_pair(record("r", "telegram", TS, text="MINOAN DIGNITY reported damage"), vessel).match_type == "name"
    assert candidate_for_pair(record("r", "telegram", TS, text="Dignity or 2111234567"), vessel).match_type == "proximity"
    single = replace(vessel, data={"name": "AMARA"})
    assert candidate_for_pair(record("r", "telegram", TS, text="Amara spoke at a meeting"), single).match_type == "proximity"
    assert candidate_for_pair(record("r", "telegram", TS, text="Tanker AMARA reported damage"), single).match_type == "name"


def test_named_civil_aircraft_is_eligible_despite_altitude():
    news = record("r", "gdelt", TS, url="https://example.test/flight-N-123AB-emergency")
    aircraft = record("a", "adsb", TS, registration="N123AB", alt_ft=35000, military=False)
    candidates = generate_candidates([news, aircraft])
    assert len(candidates) == 1 and candidates[0].match_type == "identifier"
    assert not generate_candidates([replace(news, data={"text": "aircraft overhead"}), aircraft])
    news = replace(news, data={"text": "Aircraft G-ABCD declared an emergency"})
    aircraft = replace(aircraft, data={**aircraft.data, "registration": "G-ABCD"})
    assert generate_candidates([news, aircraft])[0].match_type == "identifier"


def test_full_entities_unicode_and_distinct_reporting():
    first = record("a", "gdelt", TS, persons=["Jane Smith", "علی رضایی"], actor1="UNITED STATES", url="https://example.test/one")
    for text in ("Jane Smith reports", "علی رضایی گفت"):
        second = record("b", "gdelt", TS, text=text, url="https://example.test/two")
        assert generate_candidates([first, second])[0].match_type == "entity"
    partial = record("b", "gdelt", TS, text="Jane Jones and John Smith", actor1="UNITED STATES", orgs=["united states"])
    assert not generate_candidates([first, partial])
    same_article = replace(first, id="b")
    assert not generate_candidates([first, same_article])


def test_duplicate_event_rows_share_one_report_asset_slot():
    vessel = record("v", "ais", TS, name="BLUE STAR")
    rows = [record(str(i), "gdelt", TS, text="Vessel BLUE STAR", url="https://example.test/one") for i in range(30)]
    candidates = generate_candidates([*rows, vessel])
    assert len(candidates) == 1
    assert candidates[0].match_type == "name"


def test_batch_prioritizes_and_diversifies_and_caps_proximity():
    candidates = []
    for kind in ("ais", "adsb", "firms"):
        for i in range(5):
            c = candidate_for_pair(record(f"r:{kind}:{i}", "gdelt", TS), record(f"a:{kind}:{i}", kind, TS))
            c.match_type = "topic" if i < 2 else "proximity"
            c.candidate_score = .6 if kind == "ais" else .5
            candidates.append(c)
    chosen = select_batch(candidates, limit=12, proximity_limit=2)
    assert len(chosen) == 8
    assert {c.right.kind for c in chosen[:3]} == {"ais", "adsb", "firms"}
    assert sum(c.match_type == "proximity" for c in chosen) == 2


def test_moving_asset_cooldown_and_material_change(tmp_path):
    news = record("r", "telegram", TS, text="Vessel BLUE STAR")
    ship = record("v", "ais", TS, name="BLUE STAR", nav_status=0)
    first = candidate_for_pair(news, ship)
    moved = candidate_for_pair(news, replace(ship, lon=56.01, ts="2026-08-18T10:01:00+00:00"))
    changed = candidate_for_pair(news, replace(ship, data={**ship.data, "nav_status": 1}))
    assert first.id != moved.id
    assert coverage_key(first) == coverage_key(moved)
    assert refresh_signature(first) == refresh_signature(moved)
    assert refresh_signature(first) != refresh_signature(changed)
    state = FusionState(store=InMemoryStore(), fusion_ai=FusionAI(tmp_path, FakeOpenAI()), history=[])
    state.fusion_candidates = [moved]
    state._ai_recent[coverage_key(first)] = (1000, refresh_signature(first))
    with patch("fusion.pipeline.time.time", return_value=1100):
        state._start_ai_fusion("moving")
    assert state._last_ai_at == 0
    assert not state.fusion_assessments
    done = threading.Event()
    original = state.set_source_status
    def status(source, phase, **kwargs):
        original(source, phase, **kwargs)
        if source == "openai" and phase == "ready":
            done.set()
    state.fusion_candidates = [changed]
    with patch("fusion.pipeline.time.time", return_value=1200), patch.object(state, "set_source_status", side_effect=status):
        state._start_ai_fusion("changed")
        assert done.wait(3)
    assert len(state.fusion_assessments) == 1
    # Default cadence is 180 seconds; a new eligible pair cannot start at 179 seconds.
    state.fusion_candidates = [candidate_for_pair(replace(news, id="r:new"), ship)]
    with patch("fusion.pipeline.time.time", return_value=1379):
        state._start_ai_fusion("early")
    assert state._last_ai_at == 1200
    done.clear()
    with patch("fusion.pipeline.time.time", return_value=1380), patch.object(state, "set_source_status", side_effect=status):
        state._start_ai_fusion("due")
        assert done.wait(3)
    assert len(state.fusion_assessments) == 2
