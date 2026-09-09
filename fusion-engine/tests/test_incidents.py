"""Incidents: formation from departed cells, tracking across bins, explanations, next check, revisions."""
from __future__ import annotations

from datetime import datetime, timezone

from fusion.baseline import Baseline
from fusion.incidents import IncidentTracker
from tests.test_baseline import Ev, T0, T1, _tracks


def _iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def _scenario():
    """Cell (26,56): steady news; day-2 04-06Z military surge with degraded integrity; 05Z reporting about vessels."""
    b = Baseline(T0, T1)
    per = {i: 2 for i in range(48)}
    for i in (28, 29, 30):
        per[i] = 25
    b.add_tracks(_tracks(26, 56, per, military=True, nic=3))
    # background civilian traffic reporting good integrity keeps navint coverage adequate every hour
    civ = _tracks(26, 56, {i: 20 for i in range(48)}, military=False, nic=8)
    civ = {f"c{k}": v for k, v in civ.items()}
    b.add_tracks(civ)
    events = [Ev(f"n{i}", _iso(T0 + i * 3600 + 30), 26.5, 56.5, f"http://z/{i}", False) for i in range(48)]
    events += [Ev(f"v{a}", _iso(T0 + 29 * 3600 + 30), 26.5, 56.5, f"http://z/v/{a}", True, root_code="19") for a in range(15)]
    for e in events:
        e.themes = ["MARITIME", "TANKER attack in the strait"] if e.id.startswith("v") else ["ECON"]
        e.place, e.root_label, e.persons, e.orgs = "Strait", "Fight" if e.id.startswith("v") else "Make statement", [], []
    b.add_events(events)
    return b, events


def test_incident_forms_tracks_and_explains():
    b, events = _scenario()
    tr = IncidentTracker(b, events)
    t28 = T0 + 29 * 3600 + 10
    inc = tr.at(t28)
    assert len(inc) == 1 and inc[0].state == "new_change" and [26, 56] in inc[0].cells
    assert inc[0].streams["military"]["departed"] and inc[0].streams["navint"]["departed"]
    assert not inc[0].streams["news"]["departed"]
    assert inc[0].assessment["established"].endswith("physical change without reporting")
    # next check should point at reporting or the untested keyword prediction, not at aircraft
    assert inc[0].next_check and ("news" in inc[0].next_check["prediction"] or "keywords" in inc[0].next_check["prediction"]
                                  or "social" in inc[0].next_check["prediction"])
    # one hour later reporting arrives with vessel keywords: same incident id, a revision, leading explanation is maritime
    inc29 = tr.at(T0 + 30 * 3600 + 10)
    assert inc29[0].id == inc[0].id
    assert inc29[0].streams["news"]["departed"] or inc29[0].streams["conflict"]["departed"]
    assert any("news" in r["added"] or "conflict" in r["added"] for r in inc29[0].revisions)
    lead = inc29[0].explanations[0]
    assert lead.id == "maritime_incident", [e.to_dict() for e in inc29[0].explanations]
    kw = next(p for p in lead.predictions if p.prediction.startswith("keywords"))
    assert kw.status == "supported"
    routine = next(e for e in inc29[0].explanations if e.id == "routine_pattern")
    assert any(p.status == "contradicted" for p in routine.predictions)
    # revisions visible at t never include later ones
    assert all(r["t"] <= T0 + 29 * 3600 + 10 for r in inc29[0].revisions)
    # after the surge the incident is kept one bin as recovering, then gone
    rec = tr.at(T0 + 32 * 3600 + 10)
    assert rec and rec[0].id == inc[0].id and rec[0].state == "recovering"
    assert tr.at(T0 + 34 * 3600 + 10) == [] or all(x.id != inc[0].id for x in tr.at(T0 + 34 * 3600 + 10))


def test_no_incident_on_quiet_hours():
    b, events = _scenario()
    tr = IncidentTracker(b, events)
    assert tr.at(T0 + 40 * 3600 + 10) == []
    d = [x.to_dict() for x in tr.at(T0 + 29 * 3600 + 10)]
    assert d and set(d[0]) >= {"id", "cells", "state", "streams", "explanations", "next_check", "revisions", "assessment"}
