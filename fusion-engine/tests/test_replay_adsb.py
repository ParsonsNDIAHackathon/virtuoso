"""Build a tiny synthetic globe_history-style archive and check extract/snapshot/polyline logic."""
import gzip, io, json, tarfile, tempfile
from pathlib import Path
from fusion.replay_adsb import extract_bbox, load_tracks, snapshot_at, track_polylines, HORMUZ_BBOX

T0 = 1787356800.0  # 2026-08-18 00:00:00Z

def _trace(icao, reg, typ, mil, pts, flight=None):
    trace = []
    for i, (dt, lat, lon, alt) in enumerate(pts):
        ac = {"type": "adsb_icao", "flight": flight} if (flight and i == 0) else None
        trace.append([dt, lat, lon, alt, 250.0, 90.0, 0, 0, ac, "adsb_icao", alt, 0, 240, 0.0])
    return {"icao": icao, "r": reg, "t": typ, "dbFlags": 1 if mil else 0, "timestamp": T0, "trace": trace}

def _build(dirpath: Path):
    planes = {
        "ae1234": _trace("ae1234", "16-8200", "H47", True,  [(3600, 26.1, 56.2, 1500), (3660, 26.15, 56.25, 1600), (7200, 26.3, 56.4, 2000)], "EASY18"),
        "3c66b0": _trace("3c66b0", "D-AIUP", "A320", False, [(3600, 25.2, 55.4, 35000), (3700, 25.3, 55.6, 35000)], "DLH7YA"),
        "a24c5a": _trace("a24c5a", "N9999",  "C172", False, [(3600, 38.9, -77.0, 3000)]),   # DC, must be dropped
    }
    parts = [dirpath / "x.tar.aa", dirpath / "x.tar.ab"]
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for hexid, j in planes.items():
            data = gzip.compress(json.dumps(j).encode())
            ti = tarfile.TarInfo(f"./traces/{hexid[-2:]}/trace_full_{hexid}.json"); ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
    raw = buf.getvalue(); half = len(raw) // 2
    parts[0].write_bytes(raw[:half]); parts[1].write_bytes(raw[half:])
    return dirpath

def test_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        d = Path(d); _build(d)
        out = d / "out.json"
        kept = extract_bbox(d, HORMUZ_BBOX, out)
        assert set(kept) == {"ae1234", "3c66b0"}, kept.keys()
        assert kept["ae1234"]["military"] and kept["ae1234"]["n"] == 3
        tracks = load_tracks(out)
        snap = snapshot_at(tracks, T0 + 3700)
        ids = {t.hex: t for t in snap}
        assert ids["ae1234"].callsign == "EASY18" and ids["ae1234"].alt_ft == 1600
        assert ids["3c66b0"].lat == 25.3
        assert not snapshot_at(tracks, T0 + 3700 + 600)  # both stale after 5 min... except ae1234 point at 7200
        snap2 = snapshot_at(tracks, T0 + 7200)
        assert [t.hex for t in snap2] == ["ae1234"]
        tails = track_polylines(tracks, T0 + 3000, T0 + 4000)
        assert {t["hex"]: len(t["coords"]) for t in tails} == {"ae1234": 2, "3c66b0": 2}
        print("OK", {k: v["n"] for k, v in kept.items()})

if __name__ == "__main__":
    test_roundtrip()
