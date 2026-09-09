"""Rolling on-disk aircraft history so live baselines have more than the in-memory two hours.

One line per aircraft per 15-minute bin (the newest position in that bin), with the integrity
fields. ~1000 aircraft x 4 per hour x 48 h is a few hundred thousand short lines; the file is
trimmed to the retention window on every write. Loaded back into the replay-archive layout the
baseline reads, so live and replay share one code path.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger(__name__)
BIN_S = 15 * 60
KEEP_H = float(os.getenv("FUSION_AIRCRAFT_LOG_H", "72"))


class AircraftLog:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self._latest: dict[tuple[str, int], dict] = {}     # (hex, bin) -> row, for the current write batch
        self._last_trim = 0.0

    def record(self, tracks) -> int:
        """Append the newest position per aircraft per 15-min bin from one fuse. Returns rows written."""
        rows = []
        for tr in tracks:
            try:
                t = datetime.fromisoformat(tr.ts).timestamp()
            except Exception:
                continue
            b = int(t // BIN_S)
            key = (tr.hex, b)
            row = {"h": tr.hex, "t": round(t, 1), "la": round(tr.lat, 4), "lo": round(tr.lon, 4), "a": tr.alt_ft,
                   "m": bool(tr.military), "n": getattr(tr, "nic", None), "p": getattr(tr, "nac_p", None)}
            prev = self._latest.get(key)
            if prev is None or prev["t"] < row["t"]:
                self._latest[key] = row
                rows.append(row)
        if not rows:
            return 0
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, separators=(",", ":")) + "\n")
            now = datetime.now(timezone.utc).timestamp()
            if now - self._last_trim > 3600:
                self._trim(now)
                self._last_trim = now
        # keep the in-memory dedupe map small
        cutoff_bin = int((datetime.now(timezone.utc).timestamp() - 2 * 3600) // BIN_S)
        self._latest = {k: v for k, v in self._latest.items() if k[1] >= cutoff_bin}
        return len(rows)

    def _trim(self, now: float) -> None:
        cutoff = now - KEEP_H * 3600
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        kept = [ln for ln in lines if ln and json.loads(ln)["t"] >= cutoff]
        if len(kept) != len(lines):
            self.path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")

    def load(self, t_from: float, t_to: float) -> tuple[dict[str, dict], tuple[float, float] | None]:
        """Rows in [t_from, t_to] as {hex: {military, points:[10-field]}} plus the covered span."""
        out: dict[str, dict] = {}
        lo, hi = None, None
        try:
            with self.lock:
                text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}, None
        for ln in text.splitlines():
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            t = r["t"]
            if not (t_from <= t <= t_to):
                continue
            a = out.setdefault(r["h"], {"hex": r["h"], "military": False, "points": []})
            a["military"] = a["military"] or bool(r.get("m"))
            a["points"].append([t, r["la"], r["lo"], r.get("a"), None, None, None, "log", r.get("n"), r.get("p")])
            lo = t if lo is None or t < lo else lo
            hi = t if hi is None or t > hi else hi
        for a in out.values():
            a["points"].sort(key=lambda p: p[0])
        return out, ((lo, hi) if lo is not None else None)
