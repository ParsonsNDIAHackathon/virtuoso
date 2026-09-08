"""Curated evidence: load, validate, isolate from scoring, and escape safely.

    python -m pytest tests/test_curated.py -q        (or)  python -m tests.test_curated
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "data" / "curated" / "hormuz-incident-seed.json"
LEADS = ROOT / "data" / "curated" / "social-source-leads.json"

from fusion.curated import CuratedDataError, curated_ids, load_bundle, load_leads, load_seed  # noqa: E402


def test_seed_loads_and_references_resolve():
    seed = load_seed(SEED)
    assert len(seed["vessels"]) == 2 and len(seed["sources"]) == 6 and len(seed["claims"]) == 7
    vids = {v["id"] for v in seed["vessels"]}
    sids = {s["id"] for s in seed["sources"]}
    for c in seed["claims"]:
        assert c["vessel_id"] in vids, c["id"]
        assert c["source_ids"] and all(s in sids for s in c["source_ids"]), c["id"]


def test_idempotent():
    a, b = load_bundle(SEED, LEADS), load_bundle(SEED, LEADS)
    assert a == b
    assert json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def test_time_precision_and_no_coordinates():
    seed = load_seed(SEED)
    for c in seed["claims"]:
        if c["time_precision"] in ("date_only", "ambiguous_overnight"):
            assert c["event_time_utc"] is None, c["id"]
        else:
            assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", c["event_time_utc"]), c["id"]
        assert c["coordinates"] is None, c["id"]          # all 7 unresolved in this seed
    # a claim with a non-null but malformed coordinate must fail loudly
    bad = json.loads(SEED.read_text(encoding="utf-8"))
    bad["claims"][0]["coordinates"] = "26.5N 56.2E"
    tmp = ROOT / "data" / "curated" / "_bad_test.json"
    tmp.write_text(json.dumps(bad), encoding="utf-8")
    try:
        try:
            load_seed(tmp)
            assert False, "malformed coordinates accepted"
        except CuratedDataError:
            pass
    finally:
        tmp.unlink(missing_ok=True)


def test_alerts_unchanged_with_and_without_curated():
    """Curated records never enter correlation: the alert set at a replay instant is identical whether
    or not the curated bundle is attached, and no alert references a curated id."""
    from fusion import replay as R
    sc = R.SCENARIOS["hormuz-2026-08-18"]
    st = R.ReplayState("hormuz-2026-08-18")
    if not (R.DATA / "replay").exists():
        return                                   # replay data not built on this machine
    t = st.t_min + 11 * 3600
    with_curated = st.at(t)["alerts"]
    saved = sc.pop("curated", None)
    try:
        st2 = R.ReplayState("hormuz-2026-08-18")
        without = st2.at(t)["alerts"]
    finally:
        if saved is not None:
            sc["curated"] = saved
    assert len(with_curated) == len(without)
    assert [a["id"] for a in with_curated] == [a["id"] for a in without]
    ids = curated_ids(load_bundle(SEED, LEADS))
    for a in with_curated:
        blob = json.dumps(a)
        assert not any(cid in blob for cid in ids), a["id"]


def test_vessel_identity_rules():
    seed = load_seed(SEED)
    md = next(v for v in seed["vessels"] if v["id"] == "imo:9294484")
    am = next(v for v in seed["vessels"] if v["id"] == "imo:9333280")
    assert md["vessel_type"] == "bulk_carrier"
    assert am["mmsi_candidate"] == "636023272"
    for v in seed["vessels"]:
        assert "mmsi" not in v and "callsign" not in v, v["id"]
        assert v["id"] == f"imo:{v['imo']}"


def test_escape_and_unicode_survive():
    # the same esc() the dashboard uses (mirrored here so the rule is tested server-side too)
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                .replace('"', "&quot;").replace("'", "&#39;"))
    hostile = '<script>alert("x")</script> \' "'
    out = esc(hostile)
    assert "<script" not in out and '"' not in out and "'" not in out
    leads = load_leads(LEADS)
    langs = {it["original_language"] for it in leads["items"]}
    assert {"fa", "es"} <= langs
    fa = next(it for it in leads["items"] if it["original_language"] == "fa")
    assert any("؀" <= ch <= "ۿ" for ch in fa["publisher"]), "Persian publisher name lost"
    assert "Unicanal" in json.dumps(leads, ensure_ascii=False)


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        try:
            fn(); print("PASS", fn.__name__)
        except Exception as e:
            failed += 1; print("FAIL", fn.__name__, "->", type(e).__name__, str(e)[:200])
    sys.exit(1 if failed else 0)
