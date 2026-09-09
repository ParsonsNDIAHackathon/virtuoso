"""Baselines: departure states, insufficient evidence, expected co-occurrence, dateline config."""
from __future__ import annotations

from dataclasses import dataclass

from fusion.baseline import Baseline, cell_of
from fusion.mission import is_dateline, is_expected_cooccurrence

T0 = 1_755_388_800.0          # 2026-08-17T00:00:00Z
T1 = T0 + 2 * 86400 - 1       # two-day window, 48 hourly bins


@dataclass
class Ev:
    id: str
    ts: str
    lat: float
    lon: float
    url: str
    is_conflict: bool
    source_domain: str = "example.com"
    root_code: str = "14"
    event_code: str = "140"


def _iso(t: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def _tracks(cell_lat, cell_lon, per_hour: dict[int, int], military=False, nic=8):
    """per_hour: bin index -> number of distinct aircraft that hour."""
    tracks = {}
    k = 0
    for i, n in per_hour.items():
        for _ in range(n):
            hexid = f"h{k:04d}"; k += 1
            tracks[hexid] = {"hex": hexid, "military": military,
                             "points": [[T0 + i * 3600 + 60, cell_lat + .4, cell_lon + .4, 30000, 400, 90, None, "adsb", nic, 9]]}
    return tracks


def test_news_spike_is_new_change_and_articles_count_once():
    b = Baseline(T0, T1)
    events = []
    # steady 2 articles per hour in cell (26,56) for all 48 hours
    for i in range(48):
        for a in range(2):
            events.append(Ev(f"e{i}-{a}", _iso(T0 + i * 3600 + 30), 26.5, 56.5, f"http://x/{i}/{a}", False))
    # hour 33 (day 2, 09Z): one article yielding 10 records, plus 8 genuinely distinct articles
    for r in range(10):
        events.append(Ev(f"dup{r}", _iso(T0 + 33 * 3600 + 100), 26.5, 56.5, "http://x/same", False))
    for a in range(8):
        events.append(Ev(f"new{a}", _iso(T0 + 33 * 3600 + 200), 26.5, 56.5, f"http://x/new/{a}", False))
    b.add_events(events)
    assert b.value("news", (26, 56), 33)[0] == 2 + 1 + 8       # the 10 duplicate records count once
    d = b.score("news", (26, 56), 33)
    assert d.state == "new_change" and d.z is not None and d.z > 2 and d.reference_n >= 4
    assert b.score("news", (26, 56), 44).state == "normal"
    assert (26, 56) in b.departed_cells(T0 + 34 * 3600 + 10)


def test_persistent_then_recovering_and_insufficient_navint():
    b = Baseline(T0, T1)
    # military aircraft: 2 per hour normally, 20 per hour for bins 28..31 (day 2, 04-07Z), then back to 2
    per = {i: 2 for i in range(48)}
    for i in range(28, 32):
        per[i] = 20
    b.add_tracks(_tracks(24, 52, per, military=True))
    assert b.score("military", (24, 52), 28).state == "new_change"
    assert b.score("military", (24, 52), 31).state == "persistent"
    assert b.score("military", (24, 52), 32).state == "recovering"
    # navint: only 3 aircraft report integrity in a bin -> insufficient regardless of fraction
    thin = Baseline(T0, T1)
    thin.add_tracks(_tracks(25, 52, {i: 3 for i in range(48)}, nic=3))
    d = thin.score("navint", (25, 52), 30)
    assert d.state == "insufficient" and d.coverage == 3 and d.z is None
    # first bins of the window have too little reference to score
    early = b.score("military", (24, 52), 0)
    assert early.reference_n < 4 or early.state in ("normal", "insufficient")


def test_expected_cooccurrence_needs_two_streams():
    assert is_expected_cooccurrence((25, 55))          # DXB terminal area in the default config
    b = Baseline(T0, T1)
    per = {i: 5 for i in range(48)}
    per[33] = 60
    b.add_tracks(_tracks(25, 55, per))                 # aircraft alone spike in the airport cell
    assert b.score("tracks", (25, 55), 33).state == "new_change"
    assert (25, 55) not in b.departed_cells(T0 + 34 * 3600 + 10)
    # add a news spike in the same cell and hour -> two streams -> the cell qualifies
    events = [Ev(f"n{i}", _iso(T0 + i * 3600 + 30), 25.3, 55.3, f"http://y/{i}", False) for i in range(48)]
    events += [Ev(f"s{a}", _iso(T0 + 33 * 3600 + 30), 25.3, 55.3, f"http://y/s/{a}", True) for a in range(12)]
    b.add_events(events)
    assert (25, 55) in b.departed_cells(T0 + 34 * 3600 + 10)


def test_dateline_config_and_cell_helper():
    assert is_dateline("Dubai, Dubayy, United Arab Emirates")
    assert is_dateline("Gulf News, Dubayy, United Arab Emirates")
    assert not is_dateline("Musandam, Musandam, Oman")
    assert cell_of(26.9, 56.1) == (26, 56) and cell_of(-0.5, -0.5) == (-1, -1)


def test_timeline_reports_none_where_reference_is_thin():
    b = Baseline(T0, T1)
    b.add_tracks(_tracks(26, 56, {i: 4 for i in range(48)}))
    zs = b.timeline()["tracks"]
    assert len(zs) == 48 and all(isinstance(z, float) or z is None for z in zs)
    assert any(z is not None for z in zs)
