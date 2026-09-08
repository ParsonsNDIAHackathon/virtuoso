import asyncio
from datetime import datetime, timezone
import json

import pytest

from app.replay.controller import ReplayController
from app.replay.dataset import JsonlDataset
from app.sources.gdelt.prepare import download_rows, event_keyword_matches


def write_dataset(path):
    records = [
        {"timestamp": "2026-01-01T00:00:00Z", "event_id": "one", "payload": {"value": 1}},
        {"timestamp": "2026-01-01T00:00:00.02Z", "event_id": "two", "payload": {"value": 2}},
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


@pytest.mark.asyncio
async def test_reset_does_not_start_and_start_replays(tmp_path):
    path = tmp_path / "events.jsonl"
    write_dataset(path)
    controller = ReplayController(JsonlDataset(path), speed=100)
    await controller.reset()
    assert (await controller.status())["playing"] is False
    assert (await controller.status())["cursor"] == 0

    stream = controller.subscribe()
    iterator = stream.__aiter__()
    await controller.start()
    first = await asyncio.wait_for(iterator.__anext__(), timeout=1)
    second = await asyncio.wait_for(iterator.__anext__(), timeout=1)
    assert [first.event_id, second.event_id] == ["one", "two"]
    await stream.aclose()


def test_dataset_normalizes_and_sorts_timestamps(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text(
        json.dumps({"timestamp": "2026-01-01T01:00:00+01:00", "payload": {}}) + "\n"
        + json.dumps({"timestamp": "2026-01-01T00:00:00Z", "payload": {}}) + "\n",
        encoding="utf-8",
    )
    records = JsonlDataset(path).records
    assert records[0].timestamp == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_gdelt_csv_parser_accepts_large_fields(tmp_path):
    import io
    import zipfile

    archive_path = tmp_path / "large.zip"
    large_field = "x" * 150_000
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("record.tsv", f"date\turl\t{large_field}\n")

    rows = list(download_rows(archive_path.as_uri()))
    assert rows[0][2] == large_field


def test_event_keywords_search_structured_fields_and_url():
    event = {
        "Actor2Name": "TANKER",
        "ActionGeo_FullName": "Strait of Hormuz",
        "SOURCEURL": "https://example.test/two-uae-tankers-attacked",
    }
    assert event_keyword_matches(event, ["tanker", "hormuz", "iran"]) == ["hormuz", "tanker"]
