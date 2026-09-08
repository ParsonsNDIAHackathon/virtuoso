"""Download and spatially/temporally filter GDELT 2.0 data into replay JSONL."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import io
import json
import logging
import math
from pathlib import Path
import re
import sys
import time
from urllib.request import urlopen
import zipfile

MASTER_URL = "https://data.gdeltproject.org/gdeltv2/masterfilelist.txt"
FILE_TIMESTAMP = re.compile(r"/(\d{14})\.")
LOGGER = logging.getLogger("gdelt.prepare")

# GKG fields such as GCAM can exceed csv's 128 KiB default field limit.
csv.field_size_limit(sys.maxsize)

EVENT_COLUMNS = [
    "GLOBALEVENTID", "SQLDATE", "MonthYear", "Year", "FractionDate",
    "Actor1Code", "Actor1Name", "Actor1CountryCode", "Actor1KnownGroupCode",
    "Actor1EthnicCode", "Actor1Religion1Code", "Actor1Religion2Code",
    "Actor1Type1Code", "Actor1Type2Code", "Actor1Type3Code", "Actor2Code",
    "Actor2Name", "Actor2CountryCode", "Actor2KnownGroupCode", "Actor2EthnicCode",
    "Actor2Religion1Code", "Actor2Religion2Code", "Actor2Type1Code",
    "Actor2Type2Code", "Actor2Type3Code", "IsRootEvent", "EventCode",
    "EventBaseCode", "EventRootCode", "QuadClass", "GoldsteinScale",
    "NumMentions", "NumSources", "NumArticles", "AvgTone", "Actor1Geo_Type",
    "Actor1Geo_FullName", "Actor1Geo_CountryCode", "Actor1Geo_ADM1Code",
    "Actor1Geo_ADM2Code", "Actor1Geo_Lat", "Actor1Geo_Long", "Actor1Geo_FeatureID",
    "Actor2Geo_Type", "Actor2Geo_FullName", "Actor2Geo_CountryCode",
    "Actor2Geo_ADM1Code", "Actor2Geo_ADM2Code", "Actor2Geo_Lat", "Actor2Geo_Long",
    "Actor2Geo_FeatureID", "ActionGeo_Type", "ActionGeo_FullName",
    "ActionGeo_CountryCode", "ActionGeo_ADM1Code", "ActionGeo_ADM2Code",
    "ActionGeo_Lat", "ActionGeo_Long", "ActionGeo_FeatureID", "DATEADDED", "SOURCEURL",
]


def parse_utc(value: str) -> datetime:
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        raise ValueError("anchor must include a timezone, for example 2026-09-07T12:00:00Z")
    return timestamp.astimezone(timezone.utc)


def floor_quarter_hour(value: datetime) -> datetime:
    return value.replace(minute=value.minute - value.minute % 15, second=0, microsecond=0)


def file_timestamp(url: str) -> datetime | None:
    match = FILE_TIMESTAMP.search(url)
    return datetime.strptime(match.group(1), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc) if match else None


def list_urls() -> list[str]:
    with urlopen(MASTER_URL, timeout=60) as response:
        text = response.read().decode("utf-8")
    urls = []
    for line in text.splitlines():
        urls.extend(token for token in line.split() if token.startswith("http") and token.endswith(".zip"))
    return urls


def select_urls(urls: list[str], start: datetime, end: datetime, suffix: str) -> list[str]:
    selected = []
    for url in urls:
        if not url.endswith(suffix):
            continue
        timestamp = file_timestamp(url)
        if timestamp is not None and start <= timestamp <= end:
            selected.append(url)
    return sorted(selected)


def download_rows(url: str):
    with urlopen(url, timeout=120) as response:
        content = response.read()
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        member = archive.namelist()[0]
        with archive.open(member) as compressed:
            text = io.TextIOWrapper(compressed, encoding="utf-8", errors="replace")
            yield from csv.reader(text, delimiter="\t")


def canonical_url(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"^https?://(www\.)?", "", value)
    return value.split("#", 1)[0].split("?", 1)[0].rstrip("/")


def gkg_keyword_index(urls: list[str], keywords: list[str]) -> dict[str, list[str]]:
    index: dict[str, list[str]] = {}
    folded = [(keyword, keyword.casefold()) for keyword in keywords]
    started_at = time.monotonic()
    for file_number, url in enumerate(urls, 1):
        file_started_at = time.monotonic()
        LOGGER.info("GKG file %d/%d: %s", file_number, len(urls), url.rsplit("/", 1)[-1])
        for row in download_rows(url):
            if len(row) < 2:
                continue
            # Include DocumentIdentifier (the source URL) as well as GKG metadata.
            searchable = "\t".join(row[1:]).casefold()
            matches = [original for original, keyword in folded if keyword in searchable]
            if matches:
                index.setdefault(canonical_url(row[1]), []).extend(matches)
        elapsed = time.monotonic() - started_at
        average = elapsed / file_number
        LOGGER.info(
            "GKG file %d/%d complete in %.1f seconds; indexed URLs=%d; estimated remaining: %.1f seconds",
            file_number, len(urls), time.monotonic() - file_started_at,
            len(index), average * (len(urls) - file_number),
        )
    return {key: sorted(set(value)) for key, value in index.items()}


def event_keyword_matches(event: dict[str, str], keywords: list[str]) -> list[str]:
    """Find keyword matches in the structured Event record and source URL."""
    searchable = "\t".join(event.values()).casefold()
    return sorted({keyword for keyword in keywords if keyword.casefold() in searchable})


def within_radius(latitude: str, longitude: str, center_lat: float, center_lon: float, radius_km: float) -> bool:
    try:
        lat, lon = float(latitude), float(longitude)
    except (TypeError, ValueError):
        return False
    earth_radius_km = 6371.0088
    lat1, lat2 = math.radians(center_lat), math.radians(lat)
    delta_lat = math.radians(lat - center_lat)
    delta_lon = math.radians(lon - center_lon)
    haversine = math.sin(delta_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(delta_lon / 2) ** 2
    distance = 2 * earth_radius_km * math.asin(math.sqrt(haversine))
    return distance <= radius_km


def prepare(
    anchor: datetime,
    before_minutes: int,
    after_minutes: int,
    keywords: list[str],
    output_dir: Path,
    latitude: float = 26.57,
    longitude: float = 56.25,
    radius_km: float = 100.0,
    keyword_mode: str = "all",
) -> dict[str, int]:
    started_at = time.monotonic()
    LOGGER.info("Phase 1/4: validating request and calculating the UTC time window")
    if before_minutes < 0 or after_minutes < 0:
        raise ValueError("window values cannot be negative")
    if keyword_mode not in {"all", "matching"}:
        raise ValueError("keyword_mode must be all or matching")
    if keyword_mode == "matching" and not keywords:
        raise ValueError("matching mode requires at least one keyword")

    start = anchor - timedelta(minutes=before_minutes)
    end = anchor + timedelta(minutes=after_minutes)
    # Include neighboring buckets because GDELT file timestamps identify an
    # update bucket, while the exact DATEADDED filter below defines inclusion.
    file_start = floor_quarter_hour(start) - timedelta(minutes=15)
    file_end = floor_quarter_hour(end) + timedelta(minutes=15)

    LOGGER.info(
        "Time window: %s through %s (%d minutes); ActionGeo center=(%.4f, %.4f), radius=%.1f km",
        start.isoformat(), end.isoformat(), before_minutes + after_minutes,
        latitude, longitude, radius_km,
    )
    LOGGER.info("Phase 2/4: downloading the GDELT master file list; expected time: seconds to about a minute")
    master_started_at = time.monotonic()
    urls = list_urls()
    LOGGER.info("Master file list loaded: %d URLs in %.1f seconds", len(urls), time.monotonic() - master_started_at)
    event_urls = select_urls(urls, file_start, file_end, ".export.CSV.zip")
    gkg_urls = select_urls(urls, file_start, file_end, ".gkg.csv.zip") if keywords else []
    LOGGER.info(
        "Selected %d Event file(s) and %d GKG file(s) for %s through %s",
        len(event_urls), len(gkg_urls), file_start.isoformat(), file_end.isoformat(),
    )
    if not event_urls:
        LOGGER.warning("No Event files were selected; the output will contain zero records")

    gkg_index = {}
    if keywords:
        LOGGER.info(
            "Phase 3/4: scanning %d GKG file(s) for %d keyword(s); expected time: several seconds per file",
            len(gkg_urls), len(keywords),
        )
        gkg_started_at = time.monotonic()
        gkg_index = gkg_keyword_index(gkg_urls, keywords)
        LOGGER.info(
            "GKG scan complete: %d keyword-associated source URL(s) indexed in %.1f seconds",
            len(gkg_index), time.monotonic() - gkg_started_at,
        )
    else:
        LOGGER.info("Phase 3/4: keyword scan skipped because no keywords were supplied")

    LOGGER.info(
        "Phase 4/4: scanning %d Event file(s); expected time: several seconds per file",
        len(event_urls),
    )
    event_started_at = time.monotonic()
    records: list[dict] = []
    downloaded_rows = 0
    time_rows = 0
    geo_rows = 0
    for file_number, url in enumerate(event_urls, 1):
        file_started_at = time.monotonic()
        LOGGER.info("Event file %d/%d: %s", file_number, len(event_urls), url.rsplit("/", 1)[-1])
        for row in download_rows(url):
            downloaded_rows += 1
            if len(row) < len(EVENT_COLUMNS):
                continue
            event = dict(zip(EVENT_COLUMNS, row))
            try:
                event_time = datetime.strptime(event["DATEADDED"], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            if not start <= event_time <= end:
                continue
            time_rows += 1
            if not within_radius(event["ActionGeo_Lat"], event["ActionGeo_Long"], latitude, longitude, radius_km):
                continue
            geo_rows += 1
            matches = sorted(set(
                gkg_index.get(canonical_url(event["SOURCEURL"]), [])
                + event_keyword_matches(event, keywords)
            ))
            if keyword_mode == "matching" and not matches:
                continue
            if matches:
                event["_keyword_matches"] = matches
            records.append({
                "timestamp": event_time.isoformat().replace("+00:00", "Z"),
                "event_id": event["GLOBALEVENTID"],
                "payload": event,
            })
        elapsed = time.monotonic() - event_started_at
        average = elapsed / file_number
        remaining = average * (len(event_urls) - file_number)
        LOGGER.info(
            "Event file %d/%d complete in %.1f seconds; rows=%d, in-window=%d, inside-radius=%d; estimated remaining: %.1f seconds",
            file_number, len(event_urls), time.monotonic() - file_started_at,
            downloaded_rows, time_rows, geo_rows, remaining,
        )

    records.sort(key=lambda record: (record["timestamp"], record["event_id"]))
    LOGGER.info(
        "Filtering complete: %d rows downloaded, %d in the time window, %d inside ActionGeo radius, %d output records",
        downloaded_rows, time_rows, geo_rows, len(records),
    )
    LOGGER.info("Writing %d normalized record(s) to %s", len(records), output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    events_path = output_dir / "events.jsonl"
    with events_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, separators=(",", ":")) + "\n")
    manifest = {
        "source": "gdelt",
        "anchor": anchor.isoformat().replace("+00:00", "Z"),
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "latitude": latitude,
        "longitude": longitude,
        "radius_km": radius_km,
        "keyword_mode": keyword_mode,
        "keywords": keywords,
        "records": len(records),
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    counts = {keyword: 0 for keyword in keywords}
    for record in records:
        for keyword in record["payload"].get("_keyword_matches", []):
            counts[keyword] += 1
    LOGGER.info("Preparation complete in %.1f seconds", time.monotonic() - started_at)
    return {"records": len(records), **counts}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anchor", required=True, help="UTC ISO-8601 anchor timestamp")
    parser.add_argument("--before-minutes", type=int, default=60)
    parser.add_argument("--after-minutes", type=int, default=60)
    parser.add_argument("--keywords", nargs="*", default=[])
    parser.add_argument("--latitude", type=float, default=26.57)
    parser.add_argument("--longitude", type=float, default=56.25)
    parser.add_argument("--radius-km", type=float, default=100.0)
    parser.add_argument("--keyword-mode", choices=("all", "matching"), default="all")
    parser.add_argument("--output-dir", type=Path, default=Path("data/gdelt"))
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    keywords = [item for value in args.keywords for item in value.split(",") if item]
    summary = prepare(parse_utc(args.anchor), args.before_minutes, args.after_minutes, keywords, args.output_dir,
                      args.latitude, args.longitude, args.radius_km, args.keyword_mode)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
