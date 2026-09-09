"""Navigation-integrity stream: GNSS quality as reported by aircraft transponders.

Every ADS-B position carries the aircraft's own estimate of its navigation integrity (NIC) and
accuracy (NACp). Across many aircraft in one area those values are a public, RF-derived
observable: when a large share of aircraft in a cell report degraded integrity at the same time,
the GNSS environment there has changed. This module only counts and labels; it never names a
cause. "Navigation integrity degraded" is the strongest statement it makes.

Two things are always reported together: the degraded fraction and the number of aircraft it
rests on ("known"). Roughly half of aircraft do not transmit these fields at all, so the
denominator is aircraft that report either field, never all aircraft. A fraction built on a
handful of aircraft is flagged as insufficient evidence rather than reported as a finding.
"""
from __future__ import annotations

import math
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

# readsb semantics: NIC <= 5 means containment radius >= ~1.2 nm; NACp <= 5 means EPU >= ~0.5 nm.
# Normal en-route values are NIC 7-8, NACp 8-10.
NIC_DEGRADED = int(os.getenv("FUSION_NAVINT_NIC_MAX", "5"))
NACP_DEGRADED = int(os.getenv("FUSION_NAVINT_NACP_MAX", "5"))
MIN_KNOWN = int(os.getenv("FUSION_NAVINT_MIN_KNOWN", "15"))   # below this the fraction is not evidence


def is_degraded(nic: int | None, nac_p: int | None) -> bool | None:
    """True/False when the aircraft reports either field; None when it reports neither."""
    if nic is None and nac_p is None:
        return None
    return (nic is not None and nic <= NIC_DEGRADED) or (nac_p is not None and nac_p <= NACP_DEGRADED)


def cell_of(lat: float, lon: float, cell_deg: float = 1.0) -> tuple[int, int]:
    return (math.floor(lat / cell_deg), math.floor(lon / cell_deg))


@dataclass
class CellStat:
    cell: tuple[int, int]
    t: float
    known: set = field(default_factory=set)
    degraded: set = field(default_factory=set)

    @property
    def frac(self) -> float | None:
        return round(len(self.degraded) / len(self.known), 3) if self.known else None

    def to_dict(self, cell_deg: float = 1.0) -> dict:
        n = len(self.known)
        return {
            "t": self.t,
            "cell": [self.cell[0] * cell_deg, self.cell[1] * cell_deg],       # south-west corner
            "cell_deg": cell_deg,
            "known": n, "degraded": len(self.degraded), "frac": self.frac,
            "coverage": "insufficient" if n < MIN_KNOWN else "adequate",
        }


def series_from_tracks(tracks: dict[str, dict], t_min: float, t_max: float,
                       step_s: float = 3600.0, cell_deg: float = 1.0) -> list[CellStat]:
    """Per cell, per time bin: distinct aircraft reporting integrity, and distinct aircraft degraded.

    `tracks` is the replay archive layout {hex: {points: [[t, lat, lon, alt, gs, trk, flight, src, nic, nac_p], ...]}}.
    Points written before the integrity fields were kept have length 8 and are skipped (unknown).
    """
    stats: dict[tuple[tuple[int, int], int], CellStat] = {}
    n_bins = int((t_max + 1 - t_min) // step_s)
    for hexid, a in tracks.items():
        for p in a["points"]:
            if len(p) < 10:
                continue
            deg = is_degraded(p[8], p[9])
            if deg is None:
                continue
            i = int((p[0] - t_min) // step_s)
            if not 0 <= i < n_bins:
                continue
            key = (cell_of(p[1], p[2], cell_deg), i)
            st = stats.get(key)
            if st is None:
                st = stats[key] = CellStat(cell=key[0], t=t_min + i * step_s)
            st.known.add(hexid)
            if deg:
                st.degraded.add(hexid)
    return sorted(stats.values(), key=lambda s: (s.t, s.cell))


def window_from_tracks(tracks: dict[str, dict], t_from: float, t_to: float, cell_deg: float = 1.0) -> list[dict]:
    """One window [t_from, t_to]: per-cell known/degraded/fraction. Used by replay `at(t)`."""
    out = series_from_tracks(tracks, t_from, t_to, step_s=max(1.0, t_to - t_from + 1), cell_deg=cell_deg)
    return [s.to_dict(cell_deg) for s in out]


def cells_from_snapshot(tracks: Iterable, cell_deg: float = 1.0) -> list[dict]:
    """Live: per-cell known/degraded/fraction from AirTrack objects in the current snapshot."""
    stats: dict[tuple[int, int], CellStat] = {}
    t = None
    for tr in tracks:
        deg = is_degraded(getattr(tr, "nic", None), getattr(tr, "nac_p", None))
        if deg is None:
            continue
        c = cell_of(tr.lat, tr.lon, cell_deg)
        st = stats.get(c)
        if st is None:
            st = stats[c] = CellStat(cell=c, t=0.0)
        st.known.add(tr.id)
        if deg:
            st.degraded.add(tr.id)
    return [s.to_dict(cell_deg) for s in sorted(stats.values(), key=lambda s: s.cell)]


def timeline_counts(tracks: dict[str, dict], t_min: float, n_bins: int, step_s: float) -> tuple[list[int], list[int]]:
    """Distinct aircraft reporting integrity, and distinct degraded, per timeline bin (all cells)."""
    known = [set() for _ in range(n_bins)]
    degraded = [set() for _ in range(n_bins)]
    for hexid, a in tracks.items():
        for p in a["points"]:
            if len(p) < 10:
                continue
            deg = is_degraded(p[8], p[9])
            if deg is None:
                continue
            i = int((p[0] - t_min) // step_s)
            if 0 <= i < n_bins:
                known[i].add(hexid)
                if deg:
                    degraded[i].add(hexid)
    return [len(s) for s in known], [len(s) for s in degraded]
