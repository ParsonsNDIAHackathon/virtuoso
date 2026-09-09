"""Deterministic tests for candidate generation, structured adjudication, caching and clusters."""
from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

from fusion.fusion_ai import AIProviderError, AIUnavailable, EvidenceRecord, FusionAI, OpenAIResponses, asserted_graph_context, candidate_for_pair, generate_candidates
from fusion.ingest_adsb import AirTrack
from fusion.ingest_gdelt import OsintEvent
from fusion.ingest_telegram import SocialPost
from fusion.pipeline import FusionState
from fusion.store import InMemoryStore


def record(record_id, kind, ts, lat=26.0, lon=56.0, **data):
    return EvidenceRecord(
        id=record_id, kind=kind, label=record_id, ts=ts, timestamp_kind=f"{kind}_time",
        lat=lat, lon=lon, source=kind, data=data,
    )


class FakeOpenAI:
    available = True
    model = "fake-openai"

    def __init__(self, verdict="PLAUSIBLE"):
        self.verdict = verdict
        self.calls = 0
        self.last_input = ""

    def structured(self, instructions, input_text, name, schema):
        self.calls += 1
        self.last_input = input_text
        if name == "fusion_cluster_brief":
            return {"brief": "Multiple graph sources describe a potentially related incident.",
                    "caveats": ["Identity is not directly established."]}
        return {
            "verdict": self.verdict, "relation": "SUPPORTS", "evidence_strength": 0.67,
            "incident_relationship": "SAME_INCIDENT" if self.verdict in {"SUPPORTED", "PLAUSIBLE"} else "UNCERTAIN",
            "supporting_facts": ["Both records explicitly mention a missile."],
            "strongest_limitation": "No shared unique incident identifier.",
            "rationale": "The graph evidence is consistent but needs analyst review.",
            "resolved_entities": [{"record_id": "tg:x/1", "name": "IRGC", "canonical_name": "IRGC",
                                   "entity_type": "ORGANIZATION", "confidence": 0.9}],
        }


class FailingFusionStore(InMemoryStore):
    def record_fusion(self, candidates, assessments, clusters, batch_id):
        raise RuntimeError("simulated graph persistence failure")


def test_pair_specific_candidate_generation():
    gdelt = record("gdelt:1", "gdelt", "2026-08-18T10:00:00+00:00", actor1="IRGC", themes=["MISSILE"])
    telegram = record("tg:x/1", "telegram", "2026-08-18T10:20:00+00:00", lat=26.1, lon=56.1,
                      keywords=["missile"], text="IRGC missile report")
    telegram_2 = record("tg:x/2", "telegram", "2026-08-18T10:25:00+00:00", lat=26.15, lon=56.15,
                        keywords=["missile"], text="second missile report")
    far_telegram = record("tg:x/far", "telegram", "2026-08-18T10:25:00+00:00", lat=36, lon=66,
                          keywords=["missile"])
    routine = record("firms:routine", "firms", "2026-08-18T10:30:00+00:00", novelty=0.1)
    military = record("adsb:abc", "adsb", "2026-08-18T10:10:00+00:00", military=True, alt_ft=8000)
    candidates = generate_candidates([gdelt, telegram, telegram_2, far_telegram, routine, military])
    pairs = {frozenset((candidate.left.id, candidate.right.id)) for candidate in candidates}
    assert frozenset((gdelt.id, telegram.id)) in pairs
    assert frozenset((gdelt.id, telegram_2.id)) in pairs
    assert all("firms:routine" not in pair for pair in pairs)
    assert all("tg:x/far" not in pair for pair in pairs)
    assert any(candidate.entity_overlap for candidate in candidates if telegram.id in {candidate.left.id, candidate.right.id})


def test_adjudication_cache_and_review_semantics():
    left = record("gdelt:1", "gdelt", "2026-08-18T10:00:00+00:00", themes=["MISSILE"])
    right = record("tg:x/1", "telegram", "2026-08-18T10:10:00+00:00", text="missile")
    fake = FakeOpenAI()
    with tempfile.TemporaryDirectory() as directory:
        service = FusionAI(Path(directory), client=fake)
        first = service.adjudicate(candidate_for_pair(left, right))
        second = service.adjudicate(candidate_for_pair(left, right))
    assert first.verdict == "PLAUSIBLE" and first.needs_review
    assert second.cached and fake.calls == 1
    assert "graph_context" in fake.last_input


def test_gdelt_source_article_is_added_only_when_adjudicating():
    left = record("gdelt:doc", "gdelt", "2026-08-18T10:00:00+00:00", url="https://news.example/doc")
    right = record("tg:x/1", "telegram", "2026-08-18T10:10:00+00:00", text="missile report")
    fake = FakeOpenAI("SUPPORTED")
    document = {
        "available": True, "resolved_url": "https://news.example/doc", "title": "Incident report",
        "text": "The full source article explicitly describes the named incident and location.",
        "truncated": False, "fetched_at_epoch": 1787047200,
    }
    with tempfile.TemporaryDirectory() as directory, patch("fusion.fusion_ai.document_text", return_value=document) as fetch:
        service = FusionAI(Path(directory), client=fake)
        service.adjudicate(candidate_for_pair(left, right))
    fetch.assert_called_once_with("https://news.example/doc")
    assert "full source article explicitly describes" in fake.last_input
    assert "source_document" in fake.last_input


def test_blank_api_key_disables_calls_cleanly():
    left = record("gdelt:1", "gdelt", "2026-08-18T10:00:00+00:00")
    right = record("tg:x/1", "telegram", "2026-08-18T10:10:00+00:00")
    with tempfile.TemporaryDirectory() as directory:
        service = FusionAI(Path(directory), client=OpenAIResponses(api_key=""))
        try:
            service.adjudicate(candidate_for_pair(left, right))
        except AIUnavailable:
            pass
        else:
            raise AssertionError("blank API key should not attempt an OpenAI call")


def test_provider_error_preserves_safe_quota_detail():
    response = Mock(ok=False, status_code=429)
    response.json.return_value = {
        "error": {
            "type": "insufficient_quota",
            "code": "credit_balance_exhausted",
            "message": "You have no credits remaining. Add credits to continue using the API.",
        }
    }
    client = OpenAIResponses(api_key="test-key", model="test-model")
    with patch("fusion.fusion_ai.requests.post", return_value=response):
        try:
            client.structured("instructions", "input", "test", {"type": "object"})
        except AIProviderError as error:
            assert error.status_code == 429
            assert error.code == "credit_balance_exhausted"
            assert "no credits remaining" in str(error)
        else:
            raise AssertionError("provider errors should retain actionable detail")


def test_graph_context_excludes_proximity_and_prior_model_edges():
    entity = {
        "neighbors": [
            {"id": "actor:IRGC", "kind": "actor", "label": "IRGC"},
            {"id": "assessment:old", "kind": "assessment", "label": "old model result"},
            {"id": "adsb:x", "kind": "aircraft", "label": "nearby"},
        ],
        "links": [
            {"source": "gdelt:1", "target": "actor:IRGC", "kind": "INVOLVES"},
            {"source": "assessment:old", "target": "gdelt:1", "kind": "ASSESSES"},
            {"source": "adsb:x", "target": "gdelt:1", "kind": "NEAR"},
        ],
    }
    context = asserted_graph_context(entity, "gdelt:1")
    assert context == [{"relationship": "INVOLVES", "direction": "outgoing",
                        "node": {"id": "actor:IRGC", "kind": "actor", "label": "IRGC"}}]


def test_multimodal_clusters_and_brief():
    a = record("gdelt:1", "gdelt", "2026-08-18T10:00:00+00:00")
    b = record("tg:x/1", "telegram", "2026-08-18T10:05:00+00:00")
    c = record("firms:1", "firms", "2026-08-18T10:10:00+00:00", novelty=1.0)
    fake = FakeOpenAI("SUPPORTED")
    with tempfile.TemporaryDirectory() as directory:
        service = FusionAI(Path(directory), client=fake)
        ab = service.adjudicate(candidate_for_pair(a, b))
        bc = service.adjudicate(candidate_for_pair(b, c))
        repeated_ab = replace(ab, id="assessment:repeat", evidence_strength=0.8)
        clusters = service.clusters([ab, repeated_ab, bc])
        service.brief(clusters[0], [ab, repeated_ab, bc])
    assert clusters[0].modalities == ["firms", "gdelt", "telegram"]
    assert len(clusters[0].record_ids) == 3 and len(clusters[0].assessment_ids) == 2 and clusters[0].brief


def test_in_memory_graph_persists_ai_artifacts():
    event = OsintEvent(
        id="gdelt:1", ts="2026-08-18T10:00:00+00:00", lat=26.0, lon=56.0, place="Hormuz",
        country="IR", geo_type=4, actor1="IRGC", actor2=None, actor1_cc="IR", actor2_cc=None,
        event_code="190", root_code="19", root_label="Fight", quad_class=4, goldstein=-8,
        tone=-5, num_mentions=5, num_sources=2, url="https://example.test/report",
        source_domain="example.test", is_conflict=True, themes=["MISSILE"], persons=[], orgs=[],
    )
    post = SocialPost(id="tg:x/1", ts="2026-08-18T10:05:00+00:00", channel="x",
                      text="IRGC missile report", url="https://t.me/x/1", lat=26.02, lon=56.02,
                      place="Hormuz", keywords=["missile"], views=100, has_media=False)
    track = AirTrack(id="adsb:abc", hex="abc", ts="2026-08-18T10:05:00+00:00", lat=26.01,
                     lon=56.01, callsign="TEST1", registration=None, ac_type="H47", alt_ft=5000,
                     on_ground=False, gs_kt=120, track_deg=90, squawk=None, emergency=None,
                     category=None, military=True, source="adsb_icao", rssi=None, messages=None)
    hotspot = {"id": "firms:1", "ts": "2026-08-18T10:08:00+00:00", "lat": 26.03, "lon": 56.03,
               "novelty": 1.0, "frp": 15.0, "satellite": "N20"}
    store = InMemoryStore()
    store.ingest([event], [track], "test", hotspots=[hotspot], social_posts={post.id: post})
    store.correlate([event.id], "test", 75, 240, 0.3)
    records = __import__("fusion.fusion_ai", fromlist=["records_from_sources"]).records_from_sources(
        [event], [track], [hotspot], {post.id: post})
    candidate = candidate_for_pair(records[0], records[-1])
    fake = FakeOpenAI("SUPPORTED")
    with tempfile.TemporaryDirectory() as directory:
        service = FusionAI(Path(directory), client=fake)
        assessment = service.adjudicate(candidate)
        clusters = service.clusters([assessment])
    store.record_fusion([candidate], [assessment], clusters, "test")
    graph = store.graph([event.id], "test")
    kinds = {node["kind"] for node in graph["nodes"]}
    assert {"assessment", "cluster", "firms"} <= kinds
    assert store.fusion_assessments("test")[0]["verdict"] == "SUPPORTED"


def test_analyst_verdict_survives_graph_persistence_failure():
    event = OsintEvent(
        id="gdelt:1", ts="2026-08-18T10:00:00+00:00", lat=26.0, lon=56.0, place="Hormuz",
        country="IR", geo_type=4, actor1="IRGC", actor2=None, actor1_cc="IR", actor2_cc=None,
        event_code="190", root_code="19", root_label="Fight", quad_class=4, goldstein=-8,
        tone=-5, num_mentions=5, num_sources=2, url="https://example.test/report",
        source_domain="example.test", is_conflict=True, themes=["MISSILE"], persons=[], orgs=[],
    )
    track = AirTrack(id="adsb:abc", hex="abc", ts="2026-08-18T10:05:00+00:00", lat=26.01,
                     lon=56.01, callsign="TEST1", registration=None, ac_type="H47", alt_ft=5000,
                     on_ground=False, gs_kt=120, track_deg=90, squawk=None, emergency=None,
                     category=None, military=True, source="adsb_icao", rssi=None, messages=None)
    with tempfile.TemporaryDirectory() as directory:
        state = FusionState(store=FailingFusionStore(),
                            fusion_ai=FusionAI(Path(directory), client=FakeOpenAI("SUPPORTED")))
        state.events, state.tracks, state.batch_id = [event], [track], "test"
        result = state.adjudicate_pair("gdelt", event.id, "adsb", track.id)
    assert result["verdict"] == "SUPPORTED"
    assert state.fusion_assessments[result["id"]].verdict == "SUPPORTED"


def test_neo4j_deadlock_is_retried():
    from fusion.neo4j_store import Neo4jStore
    from neo4j.exceptions import TransientError

    deadlock = TransientError("deadlock")
    deadlock._neo4j_code = "Neo.TransientError.Transaction.DeadlockDetected"
    store = Neo4jStore.__new__(Neo4jStore)
    store.database = "neo4j"
    store.driver = Mock()
    store.driver.execute_query.side_effect = [deadlock, ([{"ok": True}], None, None)]
    with patch("fusion.neo4j_store.time.sleep"):
        assert store._query("RETURN 1") == [{"ok": True}]
    assert store.driver.execute_query.call_count == 2


if __name__ == "__main__":
    tests = [test_pair_specific_candidate_generation, test_adjudication_cache_and_review_semantics,
             test_gdelt_source_article_is_added_only_when_adjudicating,
             test_blank_api_key_disables_calls_cleanly, test_provider_error_preserves_safe_quota_detail,
             test_graph_context_excludes_proximity_and_prior_model_edges,
             test_multimodal_clusters_and_brief, test_in_memory_graph_persists_ai_artifacts,
             test_analyst_verdict_survives_graph_persistence_failure,
             test_neo4j_deadlock_is_retried]
    for test in tests:
        test(); print("PASS", test.__name__)
