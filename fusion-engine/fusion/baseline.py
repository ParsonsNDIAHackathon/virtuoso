"""Baselines: is this stream unusual for this cell at this hour, and how much evidence says so.

Every stream is reduced to one number per 1-degree cell per hour bin:

  news        distinct GDELT articles (by URL, so five records from one article count once)
  conflict    distinct conflict-coded articles
  social      geolocated social posts, all platforms
  tracks      distinct aircraft with a position in the bin
  military    distinct military-flagged aircraft
  firms_new   new thermal detections (novelty >= 0.9)
  navint      fraction of integrity-reporting aircraft that report degraded integrity

For each (stream, cell, bin) the reference set is the same hour of day, plus or minus two
hours, on every other day in the window, plus the bins two to three hours away on the same
day. The score is a robust z: (value - median) / scale, where scale is the median absolute
deviation or, for sparse counts, the Poisson scale sqrt(median + 1), whichever is larger.
With a two-day replay the reference holds roughly seven values. That is a thin baseline and
the output says so: `reference_n` is reported with every score, and fewer than four reference
values yields the state "insufficient" rather than a number.

States
  new_change     departed now, not in the previous bin
  persistent     departed for `persistent_bins` consecutive bins or more
  recovering     not departed now, departed in the previous bin
  normal         within the reference
  insufficient   too little reference, or (navint) too few reporting aircraft
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from statistics import median

from .ingest_social import is_social_event
from .mission import CONFIG, is_expected_cooccurrence
from .navint import MIN_KNOWN, is_degraded

STREAMS = ("news", "conflict", "social", "tracks", "military", "firms_new", "navint")
# streams whose departure can open an incident; "tracks" (all aircraft) is a coverage signal only
TRIGGER_STREAMS = ("news", "conflict", "social", "military", "firms_new", "navint")
COUNT_STREAMS = ("news", "conflict", "social", "tracks", "military", "firms_new")


def cell_of(lat: float, lon: float) -> tuple[int, int]:
    return (math.floor(lat), math.floor(lon))


def _ts(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


@dataclass
class Departure:
    stream: str
    cell: list[int]
    t: float
    value: float
    median: float
    scale: float
    z: float | None
    state: str
    reference_n: int
    coverage: int | None = None      # navint: aircraft reporting integrity in this bin
    expected_cooccurrence: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class Baseline:
    """Counts per stream per cell per hour over a fixed window, with robust per-bin scores."""

    def __init__(self, t_min: float, t_max: float, step_s: float = 3600.0):
        self.t_min, self.t_max, self.step = t_min, t_max, step_s
        self.n = int((t_max + 1 - t_min) // step_s)
        self.per_bin_hours = int(step_s // 3600) or 1
        # stream -> cell -> [set per bin] (distinct ids) ; navint uses two sets (known, degraded)
        self._sets: dict[str, dict[tuple[int, int], list[set]]] = {s: defaultdict(lambda: [set() for _ in range(self.n)]) for s in COUNT_STREAMS}
        self._nav_known: dict[tuple[int, int], list[set]] = defaultdict(lambda: [set() for _ in range(self.n)])
        self._nav_deg: dict[tuple[int, int], list[set]] = defaultdict(lambda: [set() for _ in range(self.n)])
        self.track_coverage: tuple[float, float] | None = None   # (t_from, t_to) with aircraft data; None = whole window

    # ---- ingestion -------------------------------------------------------------------
    def _bin(self, t: float) -> int | None:
        i = int((t - self.t_min) // self.step)
        return i if 0 <= i < self.n else None

    def add_events(self, events) -> None:
        self.__dict__.pop("_series_cache", None)
        for e in events:
            i = self._bin(_ts(e.ts))
            if i is None:
                continue
            c = cell_of(e.lat, e.lon)
            if is_social_event(e):
                self._sets["social"][c][i].add(e.id)
            else:
                key = e.url or e.id
                self._sets["news"][c][i].add(key)
                if e.is_conflict:
                    self._sets["conflict"][c][i].add(key)

    def set_track_coverage(self, t_from: float, t_to: float) -> None:
        """Live mode keeps only ~2 h of aircraft history: bins outside it have no value, not zero."""
        self.track_coverage = (t_from, t_to)
        self.__dict__.pop("_series_cache", None)

    def _tracks_covered(self, i: int) -> bool:
        if self.track_coverage is None:
            return True
        b0 = self.t_min + i * self.step
        return self.track_coverage[0] <= b0 + self.step - 1 and b0 <= self.track_coverage[1]

    def add_tracks(self, tracks: dict[str, dict]) -> None:
        self.__dict__.pop("_series_cache", None)
        for hexid, a in tracks.items():
            mil = bool(a.get("military"))
            for p in a["points"]:
                i = self._bin(p[0])
                if i is None:
                    continue
                c = cell_of(p[1], p[2])
                self._sets["tracks"][c][i].add(hexid)
                if mil:
                    self._sets["military"][c][i].add(hexid)
                if len(p) > 9:
                    deg = is_degraded(p[8], p[9])
                    if deg is not None:
                        self._nav_known[c][i].add(hexid)
                        if deg:
                            self._nav_deg[c][i].add(hexid)

    def add_firms(self, hotspots: list[dict]) -> None:
        self.__dict__.pop("_series_cache", None)
        for h in hotspots:
            if h.get("novelty", 0) < 0.9:
                continue
            i = self._bin(_ts(h["ts"]))
            if i is not None:
                self._sets["firms_new"][cell_of(h["lat"], h["lon"])][i].add(h["id"])

    # ---- series ---------------------------------------------------------------------
    def cells(self) -> set[tuple[int, int]]:
        out: set[tuple[int, int]] = set()
        for s in COUNT_STREAMS:
            out.update(self._sets[s].keys())
        out.update(self._nav_known.keys())
        return out

    def value(self, stream: str, cell: tuple[int, int], i: int) -> tuple[float | None, int | None]:
        """(value, coverage). navint value is a fraction and coverage its denominator."""
        if stream == "navint":
            known = self._nav_known.get(cell)
            if not known or not known[i]:
                return None, 0
            return len(self._nav_deg[cell][i]) / len(known[i]), len(known[i])
        if stream in ("tracks", "military") and not self._tracks_covered(i):
            return None, 0
        sets = self._sets[stream].get(cell)
        return (float(len(sets[i])) if sets else 0.0), None

    def series(self, stream: str, cell: tuple[int, int]) -> list[float | None]:
        cache = self.__dict__.setdefault("_series_cache", {})
        key = (stream, cell)
        if key not in cache:
            cache[key] = [self.value(stream, cell, i)[0] for i in range(self.n)]
        return cache[key]

    def total_series(self, stream: str) -> list[float]:
        """All cells combined, per bin (for the scrubber strip)."""
        if stream in ("tracks", "military"):
            acc = [set() for _ in range(self.n)]
            for c, sets in self._sets[stream].items():
                for i in range(self.n):
                    acc[i] |= sets[i]
            return [float(len(acc[i])) if self._tracks_covered(i) else 0.0 for i in range(self.n)]
        if stream == "navint":
            known = [set() for _ in range(self.n)]
            deg = [set() for _ in range(self.n)]
            for c in self._nav_known:
                for i in range(self.n):
                    known[i] |= self._nav_known[c][i]
                    deg[i] |= self._nav_deg[c][i]
            return [len(deg[i]) / len(known[i]) if known[i] else 0.0 for i in range(self.n)]
        acc = [set() for _ in range(self.n)]
        for c, sets in self._sets[stream].items():
            for i in range(self.n):
                acc[i] |= sets[i]
        return [float(len(a)) for a in acc]

    # ---- scoring --------------------------------------------------------------------
    def reference_indices(self, i: int) -> list[int]:
        """Same hour-of-day +/-2 h on other days; 2-3 h away on the same day."""
        per_day = int(86400 // self.step)
        day, hour = divmod(i, per_day)
        n_days = (self.n + per_day - 1) // per_day
        out = []
        for d in range(n_days):
            if d == day:
                for off in (-3, -2, 2, 3):
                    j = d * per_day + hour + off
                    if 0 <= j < self.n and 0 <= hour + off < per_day:
                        out.append(j)
            else:
                for off in (-2, -1, 0, 1, 2):
                    j = d * per_day + hour + off
                    if 0 <= j < self.n and 0 <= hour + off < per_day:
                        out.append(j)
        return out

    def score(self, stream: str, cell: tuple[int, int], i: int) -> Departure:
        vals = self.series(stream, cell)
        ref = [vals[j] for j in self.reference_indices(i) if vals[j] is not None]
        value, coverage = self.value(stream, cell, i)
        t = self.t_min + i * self.step
        cfg = CONFIG
        base = dict(stream=stream, cell=[cell[0], cell[1]], t=t, coverage=coverage,
                    expected_cooccurrence=is_expected_cooccurrence(cell))
        if value is None or len(ref) < 4 or (stream == "navint" and (coverage or 0) < MIN_KNOWN):
            return Departure(value=value if value is not None else 0.0, median=0.0, scale=0.0, z=None,
                             state="insufficient", reference_n=len(ref), **base)
        med = float(median(ref))
        mad = float(median([abs(v - med) for v in ref])) * 1.4826
        scale = max(mad, math.sqrt(med + 1.0)) if stream != "navint" else max(mad, 0.05)
        z = (value - med) / scale
        thr = float(cfg["z_threshold"])
        departed = z >= thr

        def departed_at(j: int) -> bool:
            if j < 0 or vals[j] is None:
                return False
            r = [vals[k] for k in self.reference_indices(j) if vals[k] is not None]
            if len(r) < 4:
                return False
            m = float(median(r)); s = float(median([abs(v - m) for v in r])) * 1.4826
            s = max(s, math.sqrt(m + 1.0)) if stream != "navint" else max(s, 0.05)
            return (vals[j] - m) / s >= thr

        prev = departed_at(i - 1)
        if departed:
            run = 1
            j = i - 1
            while j >= 0 and departed_at(j):
                run += 1; j -= 1
            state = "persistent" if run >= int(cfg["persistent_bins"]) else "new_change"
        else:
            state = "recovering" if prev else "normal"
        return Departure(value=round(value, 3), median=round(med, 3), scale=round(scale, 3),
                         z=round(z, 2), state=state, reference_n=len(ref), **base)

    def departures_at(self, t: float, streams=STREAMS, only_departed: bool = True) -> list[Departure]:
        i = self._bin(t)
        if i is None:
            return []
        out = []
        for cell in sorted(self.cells()):
            for s in streams:
                d = self.score(s, cell, i)
                if not only_departed or d.state in ("new_change", "persistent", "recovering"):
                    out.append(d)
        out.sort(key=lambda d: (-(d.z or 0.0), d.stream))
        return out

    def departed_cells(self, t: float) -> set[tuple[int, int]]:
        """Cells where at least one stream is currently departed on adequate evidence. In an
        expected-co-occurrence cell (airport beside a newsroom) two streams must depart."""
        by_cell: dict[tuple[int, int], set[str]] = defaultdict(set)
        for d in self.departures_at(t, streams=TRIGGER_STREAMS):
            if d.state in ("new_change", "persistent"):
                by_cell[tuple(d.cell)].add(d.stream)
        return {c for c, streams in by_cell.items() if len(streams) >= (2 if is_expected_cooccurrence(c) else 1)}

    def timeline(self) -> dict[str, list[float]]:
        """Total-series robust z per bin per stream, for the scrubber strip."""
        out = {}
        for s in STREAMS:
            vals = self.total_series(s)
            zs = []
            for i in range(self.n):
                ref = [vals[j] for j in self.reference_indices(i)]
                if len(ref) < 4:
                    zs.append(None); continue
                m = float(median(ref)); sc = float(median([abs(v - m) for v in ref])) * 1.4826
                sc = max(sc, math.sqrt(m + 1.0)) if s != "navint" else max(sc, 0.05)
                zs.append(round((vals[i] - m) / sc, 2))
            out[s] = zs
        return out
