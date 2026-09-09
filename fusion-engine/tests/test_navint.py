"""Navigation-integrity stream: degraded rule, per-cell series with coverage, snapshot cells."""
from __future__ import annotations

from fusion.ingest_adsb import AirTrack
from fusion.navint import MIN_KNOWN, cells_from_snapshot, is_degraded, series_from_tracks, timeline_counts, window_from_tracks

T0 = 1_755_388_800.0  # 2026-08-17T00:00:00Z


def _track(hexid, points, military=False):
    return {"hex": hexid, "military": military, "points": points, "n": len(points)}


def _pt(t, lat, lon, nic, nacp):
    return [t, lat, lon, 30000, 420.0, 90.0, "ABC123", "adsb_icao", nic, nacp]


def test_degraded_rule_and_unknown():
    assert is_degraded(8, 9) is False
    assert is_degraded(5, 9) is True          # NIC at threshold
    assert is_degraded(8, 4) is True          # NACp below threshold
    assert is_degraded(None, None) is None    # aircraft does not transmit the fields
    assert is_degraded(None, 3) is True


def test_series_counts_distinct_aircraft_and_reports_coverage():
    tracks = {}
    # 20 aircraft in cell (26,56) during hour 0: 12 degraded, 8 fine; each reports several points
    for i in range(20):
        nic = 4 if i < 12 else 8
        pts = [_pt(T0 + 60 * k, 26.3, 56.4, nic, 9) for k in range(5)]
        tracks[f"a{i:02d}"] = _track(f"a{i:02d}", pts)
    # 3 aircraft in cell (24,52) hour 1, all degraded: too few to count as evidence
    for i in range(3):
        tracks[f"b{i}"] = _track(f"b{i}", [_pt(T0 + 3600 + 120 * i, 24.5, 52.5, 3, 3)])
    # legacy 8-field points (extracted before the fields were kept) are ignored
    tracks["legacy"] = _track("legacy", [[T0 + 10, 26.3, 56.4, 30000, 400.0, 90.0, "OLD", "adsb_icao"]])
    series = series_from_tracks(tracks, T0, T0 + 2 * 3600 - 1, step_s=3600)
    by = {(s.cell, s.t): s for s in series}
    hot = by[((26, 56), T0)]
    assert len(hot.known) == 20 and len(hot.degraded) == 12 and hot.frac == 0.6
    d = hot.to_dict()
    assert d["coverage"] == "adequate" and d["known"] == 20 and d["cell"] == [26, 56]
    thin = by[((24, 52), T0 + 3600)].to_dict()
    assert thin["known"] == 3 and thin["frac"] == 1.0 and thin["coverage"] == "insufficient"
    assert MIN_KNOWN > 3
    # window helper: one bin over the whole span
    win = window_from_tracks(tracks, T0, T0 + 2 * 3600 - 1)
    assert {tuple(w["cell"]) for w in win} == {(26, 56), (24, 52)}
    known, deg = timeline_counts(tracks, T0, 2, 3600)
    assert known == [20, 3] and deg == [12, 3]


def test_snapshot_cells_from_live_tracks():
    def tr(hexid, lat, lon, nic, nacp):
        return AirTrack(id=f"adsb:{hexid}", hex=hexid, ts="2026-09-09T12:00:00+00:00", lat=lat, lon=lon,
                        callsign=None, registration=None, ac_type=None, alt_ft=30000, on_ground=False,
                        gs_kt=None, track_deg=None, squawk=None, emergency=None, category=None,
                        military=False, source="adsb_icao", rssi=None, messages=None, nic=nic, nac_p=nacp)
    tracks = [tr("x1", 25.5, 55.5, 3, 3), tr("x2", 25.6, 55.6, 8, 9), tr("x3", 25.7, 55.7, None, None)]
    cells = cells_from_snapshot(tracks)
    assert len(cells) == 1
    c = cells[0]
    assert c["cell"] == [25, 55] and c["known"] == 2 and c["degraded"] == 1 and c["frac"] == 0.5
    assert c["coverage"] == "insufficient"
