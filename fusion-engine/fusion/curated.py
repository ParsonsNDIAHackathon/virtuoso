"""Curated (manual, analyst-reviewed) evidence loader.

Inputs are the observatory notebook's JSON files, committed verbatim under data/curated/:
  hormuz-incident-seed.json   schema v1.0: vessels, sources, claims, ais_request, notes
  social-source-leads.json    reviewed social leads + excluded/deferred items

Rules (agreed with the spec):
  - records keep their original ids; loading is a pure function of the file (idempotent)
  - claims are claims, never observations: no coordinates are invented, date-only stays time-less,
    unknown publication times stay unknown, no confidence number is added
  - IMO is the vessel identity; MMSI / call sign stay *_candidate; no record gains a bare `mmsi`
  - these records never enter correlation or alerts (see tests/test_curated.py)
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

VALID_PRECISION = {"date_only", "ambiguous_overnight", "minute_as_reported"}
VESSEL_KEYS = {"id", "name", "imo", "vessel_type", "flag"}
SOURCE_KEYS = {"id", "publisher", "url", "source_type"}          # dates are nullable/optional in the seed
CLAIM_KEYS = {"id", "vessel_id", "source_ids", "event_date", "event_time_utc", "time_precision",
              "coordinates", "claim", "evidence_class"}                 # location_text is optional
LEAD_KEYS = {"platform", "url", "publisher", "publication_time_utc", "original_language",
             "summary_en", "summary_kind", "status"}


class CuratedDataError(ValueError):
    pass


def _need(obj: dict, keys: set, what: str):
    missing = keys - set(obj)
    if missing:
        raise CuratedDataError(f"{what} {obj.get('id', '?')}: missing keys {sorted(missing)}")


def _iso_or_none(v, what: str):
    if v is None:
        return None
    if not isinstance(v, str):
        raise CuratedDataError(f"{what}: timestamp must be a string or null, got {type(v).__name__}")
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError as e:
        raise CuratedDataError(f"{what}: bad timestamp {v!r}") from e
    return v


def _coords(v, what: str):
    if v is None:
        return None
    if (isinstance(v, (list, tuple)) and len(v) == 2
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)
            and -90 <= v[0] <= 90 and -180 <= v[1] <= 180):
        return [float(v[0]), float(v[1])]
    raise CuratedDataError(f"{what}: coordinates must be null or [lat, lon], got {v!r}")


def load_seed(path: str | Path) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    for k in ("schema_version", "vessels", "sources", "claims"):
        if k not in raw:
            raise CuratedDataError(f"seed: missing top-level key {k!r}")
    vessels, sources, claims = {}, {}, {}
    for v in raw["vessels"]:
        _need(v, VESSEL_KEYS, "vessel")
        if "mmsi" in v or "callsign" in v:
            raise CuratedDataError(f"vessel {v['id']}: MMSI/callsign must stay *_candidate")
        if not str(v["id"]).startswith("imo:"):
            raise CuratedDataError(f"vessel {v['id']}: id must be IMO-anchored (imo:<number>)")
        vessels[v["id"]] = dict(v)
    for s in raw["sources"]:
        _need(s, SOURCE_KEYS, "source")
        s = dict(s)
        for k in ("publication_date", "publication_time_utc", "as_of_date", "retrieved_date"):
            s[k] = s.get(k)                                         # nullable, kept as-is, never inferred
        s["publication_time_utc"] = _iso_or_none(s["publication_time_utc"], f"source {s['id']} publication_time_utc")
        sources[s["id"]] = s
    for c in raw["claims"]:
        _need(c, CLAIM_KEYS, "claim")
        c = dict(c)
        if c["time_precision"] not in VALID_PRECISION:
            raise CuratedDataError(f"claim {c['id']}: unknown time_precision {c['time_precision']!r}")
        if c["vessel_id"] not in vessels:
            raise CuratedDataError(f"claim {c['id']}: unknown vessel_id {c['vessel_id']!r}")
        if not c["source_ids"] or any(sid not in sources for sid in c["source_ids"]):
            raise CuratedDataError(f"claim {c['id']}: source_ids must all resolve: {c['source_ids']}")
        c["event_time_utc"] = _iso_or_none(c["event_time_utc"], f"claim {c['id']} event_time_utc")
        if c["time_precision"] in ("date_only", "ambiguous_overnight") and c["event_time_utc"] is not None:
            raise CuratedDataError(f"claim {c['id']}: {c['time_precision']} claims must not carry a time")
        if c["time_precision"] == "minute_as_reported" and c["event_time_utc"] is None:
            raise CuratedDataError(f"claim {c['id']}: minute_as_reported claim without event_time_utc")
        c["coordinates"] = _coords(c["coordinates"], f"claim {c['id']}")
        c["attacker"] = c.get("attacker")                          # nullable, never inferred
        c["location_text"] = c.get("location_text")                # optional; UI says "location not stated"
        claims[c["id"]] = c
    return {
        "schema_version": raw["schema_version"],
        "title": raw.get("title"),
        "prepared_date": raw.get("prepared_date"),
        "data_kind": raw.get("data_kind"),
        "raw_ais_observations_available": raw.get("raw_ais_observations_available", False),
        "notes": list(raw.get("notes", [])),
        "vessels": [vessels[k] for k in sorted(vessels)],
        "sources": [sources[k] for k in sorted(sources)],
        "claims": [claims[k] for k in sorted(claims)],
        "ais_request": raw.get("ais_request"),
    }


def load_leads(path: str | Path) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if "items" not in raw:
        raise CuratedDataError("leads: missing top-level key 'items'")
    items = []
    for it in raw["items"]:
        _need(it, LEAD_KEYS, "lead")
        it = dict(it)
        it["publication_time_utc"] = _iso_or_none(it["publication_time_utc"], f"lead {it['url']}")
        items.append(it)
    items.sort(key=lambda x: (x["publication_time_utc"] or "", x["url"]))
    return {
        "retrieved_date": raw.get("retrieved_date"),
        "scope": raw.get("scope"),
        "notes": list(raw.get("notes", [])),
        "items": items,
        "excluded": list(raw.get("excluded_or_deferred", [])),
    }


def load_bundle(seed_path, leads_path) -> dict:
    seed = load_seed(seed_path)
    leads = load_leads(leads_path)
    return {
        "kind": "curated_manual_evidence",
        "prepared_date": seed["prepared_date"],
        "retrieved_date": leads["retrieved_date"],
        "vessels": seed["vessels"], "sources": seed["sources"], "claims": seed["claims"],
        "leads": leads["items"], "excluded": leads["excluded"],
        "notes": seed["notes"] + leads["notes"],
        "raw_ais_observations_available": seed["raw_ais_observations_available"],
    }


def curated_ids(bundle: dict) -> set[str]:
    """Every id a curated record carries; used to prove none leak into alerts."""
    ids = {v["id"] for v in bundle["vessels"]} | {s["id"] for s in bundle["sources"]} | {c["id"] for c in bundle["claims"]}
    ids |= {v["imo"] for v in bundle["vessels"] if v.get("imo")}
    return {str(i) for i in ids}
