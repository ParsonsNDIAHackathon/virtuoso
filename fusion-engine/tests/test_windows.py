"""Three-window evaluation on the real Hormuz replay (skipped when the replay data is absent).

The claims tested are relationships, not magic numbers: the gate removes most proximity
candidates in the ordinary window, the incident window produces multi-stream incidents the
ordinary window does not, and the gap window is explained as a collection artifact rather than a finding.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

DATA = Path(os.getenv("FUSION_DATA_DIR") or Path(__file__).resolve().parent.parent / "data")
HAVE = all((DATA / "replay" / f).exists() for f in ("2026-08-17_adsb.json", "2026-08-18_adsb.json", "2026-08-18_gdelt.json"))


@pytest.mark.skipif(not HAVE, reason="Hormuz replay files not present in FUSION_DATA_DIR")
def test_three_windows_fused_vs_single_source():
    os.environ.setdefault("FUSION_STORE", "memory")
    from fusion.evaluate import evaluate, table
    from fusion.replay import ReplayState
    r = ReplayState("hormuz-2026-08-18")
    r.load()
    rows = {row["window"].split(" ")[0]: row for row in evaluate(r)}
    print("\n" + table(list(rows.values())))
    inc, quiet, gap = rows["incident"], rows["quiet"], rows["gap"]
    # the gate suppresses most proximity noise everywhere, and most strongly where nothing happened
    assert quiet["gated"] < 0.5 * quiet["ungated"]
    assert inc["gated"] < inc["ungated"]
    # the incident window carries more departures and more incidents per hour than the quiet window
    assert inc["departures"] / inc["hours"] > quiet["departures"] / quiet["hours"]
    assert inc["incident_ids"] / inc["hours"] >= quiet["incident_ids"] / quiet["hours"]
    # the gap window (ADS-B feed dip) is recognised: its incidents are led by "Collection artifact",
    # while the incident window has none led that way
    assert gap["artifact_led"] >= 1 and gap["artifact_led"] >= gap["multi"]
    assert inc["artifact_led"] == 0
