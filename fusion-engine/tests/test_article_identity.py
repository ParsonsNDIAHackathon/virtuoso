"""Article identity is deterministic; incident matching remains a separate evidence decision."""
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from fusion.fusion_ai import FusionAI, PROMPT_VERSION, candidate_for_pair
from fusion.neo4j_store import Neo4jStore
from fusion.pipeline import FusionState
from fusion.store import InMemoryStore
from fusion.source_documents import article_url_key
from tests.test_fusion_ai import FakeOpenAI, record


def news(record_id, url="https://news.example/incident", **data):
    return record(record_id, "gdelt", "2026-09-09T13:00:00+00:00", url=url, **data)


def article(url="https://news.example/incident", text="The named operation involved two attacks in different towns."):
    return {"available": True, "text": text, "title": "Incident report", "resolved_url": url,
            "truncated": False, "fetched_at_epoch": 1788960000}


def test_url_identity_preserves_content_parameters_and_rejects_homepages():
    assert article_url_key("https://NEWS.example/story/?utm_source=email&id=17#section") == article_url_key("https://news.example/story?id=17")
    assert article_url_key("https://news.example/story?id=17") != article_url_key("https://news.example/story?id=18")
    assert article_url_key("https://news.example/story?lang=en") != article_url_key("https://news.example/story?lang=fr")
    for url in ("", "https://news.example/", "https://news.example/login", "file:///etc/passwd", "https://x:bad/story"):
        assert article_url_key(url) is None


def test_same_article_remains_confirmed_when_text_is_blocked_and_incident_uncertain():
    # Regression: one report coded as assault in one town and fighting in another town.
    left = news("gdelt:a", event_code="180", actor1="GUNMEN")
    right = news("gdelt:b", event_code="190", actor1="SECURITY FORCE")
    with tempfile.TemporaryDirectory() as directory, patch("fusion.fusion_ai.document_text", return_value={"available": False, "error": "source returned HTTP 403"}) as fetch:
        service = FusionAI(Path(directory), client=FakeOpenAI("INSUFFICIENT_EVIDENCE"))
        assessment = service.adjudicate(candidate_for_pair(left, right))
        assert assessment.has_article_match
        assert assessment.article_match["confidence"] == 1.0
        assert assessment.article_match["independent_corroboration"] is False
        assert assessment.incident_relationship == "UNCERTAIN" and assessment.verdict == "INSUFFICIENT_EVIDENCE"
        assert len(set(assessment.source_groups.values())) == 1
        assert all(not doc["available"] for doc in assessment.source_documents)
        assert fetch.call_count == 1  # Fetch the shared document once per comparison.
        assert not service.clusters([assessment])
        state = FusionState(store=InMemoryStore(), fusion_ai=service)
        state.fusion_assessments[assessment.id] = assessment
        assert state.api_fusion_assessments()[0]["has_article_match"]
        store = InMemoryStore()
        store.ingest([], [], "test")
        store.record_fusion([], [assessment], [], "test")
        assert store.fusion_assessments("test")[0]["has_article_match"]


def test_redirect_identity_and_distinct_article_ids():
    left = news("gdelt:a", "http://news.example/old-incident")
    right = news("gdelt:b", "https://news.example/incident?utm_campaign=test")
    with tempfile.TemporaryDirectory() as directory, patch("fusion.fusion_ai.document_text", return_value=article()):
        service = FusionAI(Path(directory), client=FakeOpenAI())
        matched = service.adjudicate(candidate_for_pair(left, right))
        assert matched.has_article_match and matched.article_match["basis"] == "redirect_url"
    with tempfile.TemporaryDirectory() as directory, patch("fusion.fusion_ai.document_text", return_value={"available": False}):
        service = FusionAI(Path(directory), client=FakeOpenAI())
        different = service.adjudicate(candidate_for_pair(news("gdelt:a", "https://news.example/story?id=1"), news("gdelt:b", "https://news.example/story?id=2")))
        assert not different.has_article_match
        assert different.article_match["independent_corroboration"] is None


def test_common_login_redirect_is_not_an_article_match():
    with tempfile.TemporaryDirectory() as directory, patch("fusion.fusion_ai.document_text", return_value=article("https://news.example/login", "Please sign in to read this page.")):
        service = FusionAI(Path(directory), client=FakeOpenAI())
        value = service.adjudicate(candidate_for_pair(news("gdelt:a", "https://news.example/story-a"), news("gdelt:b", "https://news.example/story-b")))
        assert not value.has_article_match


def test_article_enrichment_keeps_identity_stable_and_force_replaces_verdict():
    left, right = news("gdelt:a"), news("gdelt:b")
    original = left.to_dict()
    fake = FakeOpenAI("INSUFFICIENT_EVIDENCE")
    with tempfile.TemporaryDirectory() as directory, patch("fusion.fusion_ai.document_text", return_value=article()) as fetch:
        service = FusionAI(Path(directory), client=fake)
        first = service.adjudicate(candidate_for_pair(left, right))
        cached = service.adjudicate(candidate_for_pair(left, right))
        assert cached.cached and fake.calls == 1 and left.to_dict() == original
        assert first.candidate_id == candidate_for_pair(left, right).id
        fake.verdict = "SUPPORTED"
        fresh = service.adjudicate(candidate_for_pair(left, right), force=True)
        assert not fresh.cached and fake.calls == 2 and fresh.verdict == "SUPPORTED"
        assert fresh.relation == "SAME_INCIDENT" and fresh.id == first.id
        fetch.assert_called_with(left.data["url"], force=True)
        assert fresh.source_documents[0]["characters"] > 0
        assert json.loads(fake.last_input)["article_identity"]["status"] == "SAME_ARTICLE"


def test_failed_context_does_not_cache_an_assessment_forever():
    with tempfile.TemporaryDirectory() as directory, patch("fusion.fusion_ai.document_text", return_value={"available": False}):
        service = FusionAI(Path(directory), client=FakeOpenAI("INSUFFICIENT_EVIDENCE"))
        candidate = candidate_for_pair(news("gdelt:a"), news("gdelt:b"))
        value = service.adjudicate(candidate).to_dict()
        value["created_at"] = (datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat()
        key = service._cache_key(candidate)
        service.cache.put(key, "assessment", value)
        assert service.cached_assessment(candidate) is None


def test_related_incidents_get_their_own_relation():
    fake = FakeOpenAI("SUPPORTED")
    original = fake.structured
    fake.structured = lambda *args: {**original(*args), "incident_relationship": "RELATED_INCIDENTS"}
    with tempfile.TemporaryDirectory() as directory, patch("fusion.fusion_ai.document_text", return_value=article()):
        service = FusionAI(Path(directory), client=fake)
        value = service.adjudicate(candidate_for_pair(news("gdelt:a"), news("gdelt:b")))
        assert value.verdict == "SUPPORTED" and value.relation == "OPERATIONALLY_RELATED"
        assert value.has_article_match and value.incident_relationship == "RELATED_INCIDENTS"


def test_confidence_in_unrelated_events_is_not_positive_link_strength():
    for verdict, incident in (("CONTRADICTED", "UNRELATED"), ("SUPPORTED", "UNCERTAIN")):
        fake = FakeOpenAI(verdict)
        original = fake.structured
        fake.structured = lambda *args: {**original(*args), "incident_relationship": incident, "evidence_strength": 0.99}
        with tempfile.TemporaryDirectory() as directory, patch("fusion.fusion_ai.document_text", return_value=article()):
            service = FusionAI(Path(directory), client=fake)
            value = service.adjudicate(candidate_for_pair(news("gdelt:a"), news("gdelt:b")))
            assert value.has_article_match and value.incident_relationship == incident
            assert value.verdict == "INSUFFICIENT_EVIDENCE" and value.relation == "NONE"
            assert value.evidence_strength == 0 and not service.clusters([value])


def test_duplicate_article_records_do_not_boost_cluster_corroboration():
    left, duplicate = news("gdelt:a"), news("gdelt:b")
    other = record("tg:x/1", "telegram", "2026-09-09T13:00:00+00:00")
    with tempfile.TemporaryDirectory() as directory, patch("fusion.fusion_ai.document_text", return_value=article()):
        service = FusionAI(Path(directory), client=FakeOpenAI("SUPPORTED"))
        first = service.adjudicate(candidate_for_pair(left, other))
        repeated = service.adjudicate(candidate_for_pair(duplicate, other))
        internal = service.adjudicate(candidate_for_pair(left, duplicate))
        baseline = service.clusters([first])[0]
        cluster = service.clusters([first, repeated, internal])[0]
        assert cluster.score == baseline.score
        assert len(cluster.record_ids) == 3 and cluster.caveats
        service.brief(cluster, [first, repeated, internal])
        assert any("same article" in caveat for caveat in cluster.caveats)


def test_neo4j_roundtrip_preserves_article_and_context_results():
    with tempfile.TemporaryDirectory() as directory, patch("fusion.fusion_ai.document_text", return_value=article()):
        service = FusionAI(Path(directory), client=FakeOpenAI("INSUFFICIENT_EVIDENCE"))
        assessment = service.adjudicate(candidate_for_pair(news("gdelt:a"), news("gdelt:b")))
    store = Neo4jStore.__new__(Neo4jStore)
    store._query = Mock(return_value=[])
    store.record_fusion([], [assessment], [], "test")
    row = store._query.call_args.kwargs["rows"][0]
    assert row["has_article_match"] and row["incident_relationship"] == "UNCERTAIN"
    assert not any(isinstance(value, dict) for value in row.values())
    store._query.return_value = [{"a": row}]
    value = store.fusion_assessments("test")[0]
    assert value["article_match"] == assessment.article_match
    assert value["source_documents"] == assessment.source_documents
    assert value["source_groups"] == assessment.source_groups


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_") and callable(test):
            test()
            print("PASS", name)
