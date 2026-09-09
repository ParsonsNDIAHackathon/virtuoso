"""Compatibility between the incident/social engine and AIS/AI snapshot workflows."""
from dataclasses import replace
from unittest.mock import Mock, patch

from fusion.aoi_summary import summarize_aoi
from fusion.fusion_ai import FusionAI, candidate_for_pair, generate_candidates, records_from_sources
from fusion.ingest_social import SocialPost, social_to_event
from fusion.pipeline import FusionState
from fusion.retrieval import select_batch
from fusion.store import InMemoryStore
from tests.test_ais_fusion import evidence
from tests.test_aoi_summary import Summarizer
from tests.test_fusion_ai import FakeOpenAI, record


def post(kind, ts):
    prefix = {"reddit": "reddit", "bluesky": "bsky", "mastodon": "mastodon"}[kind]
    return SocialPost(id=f"{prefix}:test", platform=kind, ts=ts, channel="test",
                      text="Tanker MMSI 211123456 reported damage", url=f"https://{kind}.test/post",
                      lat=26, lon=56, place="Gulf", keywords=["tanker"], views=1, has_media=False)


def test_new_social_platforms_rank_against_ais_and_enter_aoi_summary(tmp_path):
    vessel, _ = evidence()
    vessel["mmsi"] = 211123456
    posts = [post(kind, vessel["ts"]) for kind in ("reddit", "bluesky", "mastodon")]
    records = records_from_sources([social_to_event(p) for p in posts], [], [],
                                   {p.id: p for p in posts}, vessels=[vessel])
    candidates = generate_candidates(records)
    assert {c.left.kind for c in candidates if c.right.kind == "ais" and c.match_type == "identifier"} == {"reddit", "bluesky", "mastodon"}
    model = Summarizer()
    region = {"id": "gulf", "name": "Gulf", "lat": 26, "lon": 56, "radius_nm": 60}
    result = summarize_aoi(FusionAI(tmp_path, model), region, records)
    assert result["total_records"] == 4
    assert all(result["counts"][kind] == 1 for kind in ("reddit", "bluesky", "mastodon", "ais"))
    assert {r["id"] for r in model.inputs[0]["records"]} == {r.id for r in records}


def test_manual_assessment_survives_incident_gate_but_drops_when_claims_change(tmp_path):
    vessel, _ = evidence()
    report = post("reddit", vessel["ts"])
    analysis = Mock()
    analysis.baseline.departed_cells.return_value = set()
    state = FusionState(store=InMemoryStore(), fusion_ai=FusionAI(tmp_path, FakeOpenAI()),
                        live_analysis=analysis, history=[])
    state.social = [social_to_event(report)]
    state.social_posts = {report.id: report}
    state.ais_snapshot = lambda: {"vessels": [vessel]}
    result = state.adjudicate_pair("reddit", report.id, "ais", vessel["id"])
    with patch("fusion.pipeline.HISTORY_FILE", tmp_path / "history.jsonl"), patch.object(state, "_start_ai_fusion"):
        vessel["lon"] += .01
        state.fuse()
        assert not state.fusion_candidates
        assert result["id"] in state.fusion_assessments
        report.text = "Correction: the earlier damage claim was withdrawn"
        state.social = [social_to_event(report)]
        state.fuse()
        assert result["id"] not in state.fusion_assessments


def test_incident_priority_stays_within_source_evidence_tier():
    news = record("news", "reddit", "2026-08-18T10:00:00+00:00")
    ship = record("ship", "ais", news.ts)
    pair = candidate_for_pair(news, ship)
    high = replace(pair, id="strong", match_type="identifier", candidate_score=.95)
    away = replace(pair, id="away", left=replace(news, id="away"), match_type="topic", candidate_score=.56)
    incident = replace(pair, id="incident", left=replace(news, id="incident"), match_type="topic", candidate_score=.5)
    chosen = select_batch([away, incident, high], limit=3, priority=lambda c: c.id == "incident")
    assert [c.id for c in chosen] == ["strong", "incident", "away"]
