"""Article extraction used to enrich GDELT adjudication records."""
from unittest.mock import patch

from fusion.source_documents import FAILURE_TTL, _cache, document_text


def test_article_paragraphs_are_extracted_without_page_chrome():
    page = """
    <html><head><title>Test report</title><script>ignore me</script></head><body>
      <nav><p>Navigation should not become evidence.</p></nav>
      <article><h1>Incident update</h1><p>The first substantive paragraph names the vessel and location.</p>
      <p>The second substantive paragraph supplies corroborating detail.</p></article>
    </body></html>
    """
    with patch("fusion.source_documents._safe_html", return_value=(page, "https://news.example/report", 200)):
        result = document_text("https://news.example/report?test=article-extraction")
    assert result["available"] and result["title"] == "Test report"
    assert "first substantive paragraph" in result["text"]
    assert "Navigation should not" not in result["text"] and "ignore me" not in result["text"]


def test_failed_fetch_retries_after_short_ttl():
    url = "https://news.example/retry"
    page = "<p>The publisher now permits access to this substantive article.</p>"
    with patch.dict(_cache, {}, clear=True), patch("fusion.source_documents.time.time", return_value=1000) as clock, patch(
        "fusion.source_documents._safe_html", side_effect=[ValueError("source returned HTTP 403"), (page, url, 200)]
    ) as fetch:
        assert not document_text(url)["available"]
        assert not document_text(url)["available"]
        assert fetch.call_count == 1
        clock.return_value = 1000 + FAILURE_TTL + 1
        assert document_text(url)["available"]
        assert fetch.call_count == 2


def test_force_bypasses_successful_document_cache():
    url = "https://news.example/updated"
    with patch.dict(_cache, {}, clear=True), patch("fusion.source_documents._safe_html", side_effect=[
        ("<p>The initial article contains the first account of this event.</p>", url, 200),
        ("<p>The updated article adds the confirmed location of this event.</p>", url, 200),
    ]) as fetch:
        assert "initial article" in document_text(url)["text"]
        assert "initial article" in document_text(url)["text"]
        assert "updated article" in document_text(url, force=True)["text"]
        assert fetch.call_count == 2


def test_truncation_is_reported_for_cached_and_internal_text_limits():
    url = "https://news.example/long"
    page = "<p>" + "Substantive incident detail. " * 5000 + "</p>"
    with patch.dict(_cache, {}, clear=True), patch("fusion.source_documents._safe_html", return_value=(page, url, 200)) as fetch:
        short = document_text(url, max_chars=1000)
        assert short["truncated"] and len(short["text"]) == 1000
        long = document_text(url, max_chars=200_000)
        assert long["truncated"] and len(long["text"]) == 100_000
        assert fetch.call_count == 1


if __name__ == "__main__":
    for name, test in list(globals().items()):
        if name.startswith("test_") and callable(test):
            test()
            print("PASS", name)
