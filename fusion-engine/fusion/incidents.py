"""Incidents: departed cells grouped into a persistent object, explanations tested against
evidence, and the next check that would separate them.

An incident is a connected group of departed cells tracked across hour bins. It is a
deterministic function of the data in the window, so a replay instant t shows exactly the
incidents that were formed from evidence available at t, with their revision log up to t.

Explanations come from mission configuration. Each one states predictions; every prediction
is resolved against the streams as supported, contradicted, or untested (untested when the
evidence that would decide it is missing or its coverage is inadequate). No percentages: an
explanation is reported by how many of its predictions are met, and the analyst sees each one.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass, field

from .baseline import Baseline, STREAMS, TRIGGER_STREAMS, cell_of
from .ingest_social import is_social_event
from .mission import CONFIG

PHYSICAL = ("tracks", "military", "firms_new", "navint")
REPORTING = ("news", "conflict", "social")

# Explanation templates. Predictions are (kind, argument) pairs:
#   departed:<stream>        that stream departed in the incident cells or their neighbours
#   quiet:<stream>           that stream did NOT depart (with adequate coverage)
#   keywords:<a|b|c>         reporting in the cells mentions any of these terms
#   coverage_drop:tracks     distinct aircraft fell well below the reference (feed dip)
#   prior_day_same           the same hour on the prior day was also elevated (routine pattern)
DEFAULT_EXPLANATIONS = [
    {"id": "maritime_incident", "title": "Maritime or coastal incident with physical signature",
     "predictions": ["departed:navint", "departed:military", "keywords:vessel|tanker|ship|strait|hormuz|projectile|attack", "departed:news"]},
    {"id": "air_activity", "title": "Military air or missile activity",
     "predictions": ["departed:military", "departed:navint", "keywords:missile|drone|airspace|notam|strike", "departed:social"]},
    {"id": "reporting_only", "title": "Reporting surge without a physical change",
     "predictions": ["departed:news", "quiet:military", "quiet:navint", "quiet:firms_new"]},
    {"id": "routine_pattern", "title": "Routine daily pattern",
     "predictions": ["prior_day_same", "quiet:news"]},
    {"id": "collection_artifact", "title": "Collection artifact",
     "predictions": ["coverage_drop:tracks"]},
]

NEXT_CHECKS = {
    "departed:navint": "More integrity-reporting aircraft in the cell over the next hour (ADS-B, continuous)",
    "departed:military": "Military-flagged aircraft positions in the cell over the next hour (ADS-B, continuous)",
    "departed:news": "GDELT events geocoded to the cell in the next two 15-minute windows",
    "departed:social": "Geolocated posts on any configured platform for the cell (5-minute cadence)",
    "keywords": "Article text of the departed cell's reporting (on-demand fetch through adjudication)",
    "departed:firms_new": "Next VIIRS pass over the cell (roughly twice daily)",
    "quiet:navint": "Integrity-reporting aircraft in the cell (need at least the minimum count)",
    "coverage_drop:tracks": "ADS-B feed health for the region (compare with the previous hour's count)",
    "prior_day_same": "Same hour on additional prior days (extend the replay window)",
}


@dataclass
class PredictionResult:
    prediction: str
    status: str            # supported | contradicted | untested
    evidence: str


@dataclass
class Explanation:
    id: str
    title: str
    predictions: list[PredictionResult]

    @property
    def supported(self) -> int:
        return sum(p.status == "supported" for p in self.predictions)

    @property
    def contradicted(self) -> int:
        return sum(p.status == "contradicted" for p in self.predictions)

    def to_dict(self) -> dict:
        return {"id": self.id, "title": self.title, "supported": self.supported, "contradicted": self.contradicted,
                "untested": len(self.predictions) - self.supported - self.contradicted,
                "predictions": [asdict(p) for p in self.predictions]}


@dataclass
class Incident:
    id: str
    cells: list[list[int]]
    first_t: float
    last_t: float
    state: str                                  # new_change | persistent | recovering
    streams: dict[str, dict]                    # stream -> strongest departure in the incident cells
    explanations: list[Explanation]
    next_check: dict | None
    revisions: list[dict] = field(default_factory=list)
    assessment: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "cells": self.cells, "first_t": self.first_t, "last_t": self.last_t, "state": self.state,
                "streams": self.streams, "explanations": [e.to_dict() for e in self.explanations],
                "next_check": self.next_check, "revisions": self.revisions, "assessment": self.assessment}


def _components(cells: set[tuple[int, int]]) -> list[set[tuple[int, int]]]:
    seen, out = set(), []
    for c in sorted(cells):
        if c in seen:
            continue
        comp, stack = set(), [c]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    y = (x[0] + dy, x[1] + dx)
                    if y in cells and y not in comp:
                        stack.append(y)
        seen |= comp
        out.append(comp)
    return out


class IncidentTracker:
    """Forms incidents per hour bin from the baseline and tracks them across bins by cell overlap."""

    def __init__(self, baseline: Baseline, events, explanations: list[dict] | None = None):
        self.b = baseline
        self.events = list(events)
        self.explanations = explanations or CONFIG.get("explanations") or DEFAULT_EXPLANATIONS
        self._by_bin: dict[int, list[Incident]] = {}
        self._build()

    # ---- evidence helpers -------------------------------------------------------------
    def _neigh(self, cells: set[tuple[int, int]]) -> set[tuple[int, int]]:
        return {(c[0] + dy, c[1] + dx) for c in cells for dy in (-1, 0, 1) for dx in (-1, 0, 1)}

    def _stream_state(self, stream: str, cells: set[tuple[int, int]], i: int) -> dict:
        """Strongest departure of a stream in the cells or their neighbours, and whether coverage allowed a verdict."""
        best, any_adequate = None, False
        for c in self._neigh(cells):
            d = self.b.score(stream, c, i)
            if d.state != "insufficient":
                any_adequate = True
            if d.state in ("new_change", "persistent") and (best is None or (d.z or 0) > (best.z or 0)):
                best = d
        return {"departed": best is not None, "adequate": any_adequate,
                "best": best.to_dict() if best else None}

    def _keywords_present(self, cells: set[tuple[int, int]], i: int, words: list[str]) -> tuple[bool, int]:
        t0 = self.b.t_min + i * self.b.step
        t1 = t0 + self.b.step
        neigh = self._neigh(cells)
        hits, n = 0, 0
        from datetime import datetime
        for e in self.events:
            ts = datetime.fromisoformat(e.ts).timestamp()
            if not (t0 <= ts < t1) or cell_of(e.lat, e.lon) not in neigh:
                continue
            n += 1
            text = " ".join(str(x) for x in [e.place, e.root_label, *(getattr(e, "themes", None) or []),
                                             *(getattr(e, "persons", None) or []), *(getattr(e, "orgs", None) or [])]).lower()
            if any(w in text for w in words):
                hits += 1
        return hits > 0, n

    def _prior_day_same(self, cells: set[tuple[int, int]], i: int, streams: dict) -> str:
        per_day = int(86400 // self.b.step)
        if i - per_day < 0:
            return "untested"
        # the routine explanation predicts the prior day at this hour was elevated for the departed streams
        for s, st in streams.items():
            if st["departed"] and s in PHYSICAL:
                vals = [self.b.value(s, c, i - per_day)[0] or 0.0 for c in cells]
                now = [self.b.value(s, c, i)[0] or 0.0 for c in cells]
                if sum(now) > 0 and sum(vals) >= 0.6 * sum(now):
                    return "supported"
                return "contradicted"
        return "untested"

    def _coverage_drop(self, cells: set[tuple[int, int]], i: int) -> str:
        tot_now = sum(self.b.value("tracks", c, i)[0] or 0.0 for c in self._neigh(cells))
        ref = [sum(self.b.value("tracks", c, j)[0] or 0.0 for c in self._neigh(cells)) for j in self.b.reference_indices(i)]
        if len(ref) < 4:
            return "untested"
        from statistics import median
        med = median(ref)
        if med <= 0:
            return "untested"
        return "supported" if tot_now < 0.5 * med else "contradicted"

    # ---- explanation evaluation --------------------------------------------------------
    def _evaluate(self, cells: set[tuple[int, int]], i: int, streams: dict) -> list[Explanation]:
        out = []
        for tpl in self.explanations:
            preds = []
            for p in tpl["predictions"]:
                kind, _, arg = p.partition(":")
                if kind == "departed":
                    st = streams[arg]
                    if st["departed"]:
                        b = st["best"]
                        preds.append(PredictionResult(p, "supported", f"{arg} z={b['z']} in cell {b['cell']} ({b['state']}, ref {b['reference_n']})"))
                    elif st["adequate"]:
                        preds.append(PredictionResult(p, "contradicted", f"{arg} within reference in the incident cells"))
                    else:
                        preds.append(PredictionResult(p, "untested", f"{arg} coverage insufficient in the incident cells"))
                elif kind == "quiet":
                    st = streams[arg]
                    if st["departed"]:
                        preds.append(PredictionResult(p, "contradicted", f"{arg} departed (z={st['best']['z']})"))
                    elif st["adequate"]:
                        preds.append(PredictionResult(p, "supported", f"{arg} within reference"))
                    else:
                        preds.append(PredictionResult(p, "untested", f"{arg} coverage insufficient"))
                elif kind == "keywords":
                    words = [w.strip().lower() for w in arg.split("|") if w.strip()]
                    hit, n = self._keywords_present(cells, i, words)
                    if n == 0:
                        preds.append(PredictionResult(p, "untested", "no reporting in the incident cells this hour"))
                    else:
                        preds.append(PredictionResult(p, "supported" if hit else "contradicted", f"{n} reports checked for {', '.join(words[:4])}"))
                elif kind == "prior_day_same":
                    status = self._prior_day_same(cells, i, streams)
                    preds.append(PredictionResult(p, status, "prior day at this hour compared for the departed physical streams"))
                elif kind == "coverage_drop":
                    status = self._coverage_drop(cells, i)
                    preds.append(PredictionResult(p, status, "distinct aircraft this hour vs the reference median"))
                else:
                    preds.append(PredictionResult(p, "untested", "unknown prediction type"))
            out.append(Explanation(tpl["id"], tpl["title"], preds))
        # rank by supported minus twice contradicted, then by share of predictions supported, so a
        # template padded with 'quiet' predictions cannot outrank one whose positive predictions were met
        out.sort(key=lambda e: (-(e.supported - 2 * e.contradicted), -(e.supported / max(1, len(e.predictions))), e.contradicted))
        return out

    def _next_check(self, explanations: list[Explanation]) -> dict | None:
        """The untested prediction that appears in the most explanations decides the check."""
        counts: dict[str, int] = defaultdict(int)
        for e in explanations:
            for p in e.predictions:
                if p.status == "untested":
                    counts[p.prediction] += 1
        if not counts:
            # nothing untested: recommend the check that would most change the leading explanation,
            # else the runner-up's first unmet prediction
            lead = explanations[0] if explanations else None
            if lead and not lead.contradicted and len(explanations) > 1:
                alt = explanations[1]
                p = next((x for x in alt.predictions if x.status != 'supported'), None)
                if p:
                    key = p.prediction.split(':')[0] if p.prediction.startswith('keywords') else p.prediction
                    return {'prediction': p.prediction, 'separates': 1, 'source': NEXT_CHECKS.get(key, 'analyst review'),
                            'why': f"would raise the runner-up '{alt.title}' against '{lead.title}'"}
            if lead and lead.contradicted:
                p = next(x for x in lead.predictions if x.status == "contradicted")
                key = p.prediction.split(":")[0] if p.prediction.startswith("keywords") else p.prediction
                return {"prediction": p.prediction, "separates": 1, "source": NEXT_CHECKS.get(key, "analyst review"),
                         "why": f"would reverse the contradiction under '{lead.title}'"}
            return None
        pred = max(counts, key=lambda k: (counts[k], k))
        key = pred.split(":")[0] if pred.startswith("keywords") else pred
        return {"prediction": pred, "separates": counts[pred], "source": NEXT_CHECKS.get(key, "analyst review"),
                "why": f"untested under {counts[pred]} explanation(s)"}

    # ---- tracking ---------------------------------------------------------------------
    def _build(self) -> None:
        prev: list[Incident] = []
        seq = 0
        for i in range(self.b.n):
            t = self.b.t_min + i * self.b.step
            departed = self.b.departed_cells(self.b.bin_end(i))      # evaluate bin i once it has completed
            current: list[Incident] = []
            unused = list(prev)
            for comp in _components(departed):
                streams = {s: self._stream_state(s, comp, i) for s in STREAMS}
                explanations = self._evaluate(comp, i, streams)
                match = next((p for p in unused if set(map(tuple, p.cells)) & self._neigh(comp)), None)
                if match:
                    unused.remove(match)          # a split produces one continuation and new ids for the rest
                states = [streams[s]["best"]["state"] for s in streams if streams[s]["departed"]]
                state = "persistent" if match and "persistent" in states else "new_change"
                if match:
                    inc = Incident(id=match.id, cells=sorted(map(list, comp)), first_t=match.first_t, last_t=t, state=state,
                                   streams=streams, explanations=explanations, next_check=self._next_check(explanations),
                                   revisions=list(match.revisions))
                    added = [s for s in STREAMS if streams[s]["departed"] and not match.streams.get(s, {}).get("departed")]
                    gone = [s for s in STREAMS if match.streams.get(s, {}).get("departed") and not streams[s]["departed"]]
                    if added or gone or set(map(tuple, comp)) != set(map(tuple, match.cells)):
                        inc.revisions.append({"t": t, "added": added, "gone": gone, "cells": len(comp),
                                              "leading": explanations[0].title if explanations else None})
                else:
                    seq += 1
                    inc = Incident(id=f"incident:{seq:03d}", cells=sorted(map(list, comp)), first_t=t, last_t=t, state="new_change",
                                   streams=streams, explanations=explanations, next_check=self._next_check(explanations),
                                   revisions=[{"t": t, "added": [s for s in STREAMS if streams[s]["departed"]], "gone": [],
                                               "cells": len(comp), "leading": explanations[0].title if explanations else None}])
                inc.assessment = self._assess(inc)
                current.append(inc)
            # incidents that ended this bin are kept one more bin as "recovering"
            for p in prev:
                if not any(set(map(tuple, p.cells)) & self._neigh(set(map(tuple, c.cells))) for c in current) and p.state != "recovering":
                    r = Incident(id=p.id, cells=p.cells, first_t=p.first_t, last_t=t, state="recovering", streams=p.streams,
                                 explanations=p.explanations, next_check=p.next_check,
                                 revisions=p.revisions + [{"t": t, "added": [], "gone": [s for s in STREAMS if p.streams[s]["departed"]],
                                                           "cells": len(p.cells), "leading": None}])
                    r.assessment = self._assess(r)
                    current.append(r)
            self._by_bin[i] = current
            prev = current

    def _assess(self, inc: Incident) -> dict:
        departed = [s for s in STREAMS if inc.streams[s]["departed"]]
        physical = [s for s in departed if s in PHYSICAL]
        reporting = [s for s in departed if s in REPORTING]
        insufficient = [s for s in STREAMS if not inc.streams[s]["adequate"]]
        lead = inc.explanations[0] if inc.explanations else None
        established = (f"{', '.join(departed)} departed from their hour-of-day reference in {len(inc.cells)} cell(s)"
                       if departed else "no stream currently departed")
        if physical and reporting:
            established += "; physical and reporting streams moved together"
        elif physical:
            established += "; physical change without reporting"
        elif reporting:
            established += "; reporting without a measured physical change"
        disputed = (f"leading explanation '{lead.title}' has {lead.contradicted} contradicted prediction(s)"
                    if lead and lead.contradicted else "no contradicted predictions under the leading explanation")
        unresolved = (f"coverage insufficient for {', '.join(insufficient)}" if insufficient else "all streams had adequate coverage")
        return {"established": established, "disputed": disputed, "unresolved": unresolved,
                "leading": lead.title if lead else None,
                "relevance": f"{len(inc.cells)} cell(s) in the monitored area; {inc.state}"}

    def at(self, t: float) -> list[Incident]:
        i = self.b.completed_bin(t)
        if i is None:
            return []
        out = []
        for inc in self._by_bin.get(i, []):
            # only revisions available at t
            revs = [r for r in inc.revisions if r["t"] <= t]
            out.append(Incident(**{**asdict_shallow(inc), "revisions": revs}))
        return out


def asdict_shallow(inc: Incident) -> dict:
    return {"id": inc.id, "cells": inc.cells, "first_t": inc.first_t, "last_t": inc.last_t, "state": inc.state,
            "streams": inc.streams, "explanations": inc.explanations, "next_check": inc.next_check,
            "revisions": inc.revisions, "assessment": inc.assessment}
