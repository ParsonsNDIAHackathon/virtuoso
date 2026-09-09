"""Three-window evaluation: does fusion beat either source alone, and does it stay quiet when it should?

Runs the replay engine hourly across three windows and tabulates, per window:
  ungated       proximity candidates with no baseline gate (what the engine produced before)
  gated         candidates after the baseline gate
  incidents     distinct incidents active in the window
  multi         incidents with a physical stream and a reporting stream both departed
  insufficient  incidents that could not test navigation integrity for lack of reporting aircraft
  reporting_only / physical_only   single-source departures that fusion did NOT promote to multi
  artifact_led  incidents whose leading explanation is "Collection artifact" (feed dip recognised)

The point is not a score. It is that an analyst can see the engine produce more in the incident
window, stay quiet in the ordinary window, and say "insufficient" in the gap window instead of
inventing a finding. Run: python -m fusion.evaluate [scenario]
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone

from .incidents import PHYSICAL, REPORTING

DEFAULT_WINDOWS = {
    "incident (18 Aug 04-12Z)": ("2026-08-18T04:00", "2026-08-18T12:00"),
    "quiet (17 Aug 00-08Z)":    ("2026-08-17T00:00", "2026-08-17T08:00"),
    "gap (18 Aug 15-17Z)":      ("2026-08-18T15:00", "2026-08-18T17:00"),
}


def _t(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


def evaluate(replay, windows: dict[str, tuple[str, str]] | None = None, step_s: float = 3600.0) -> list[dict]:
    rows = []
    for name, (a, b) in (windows or DEFAULT_WINDOWS).items():
        t = _t(a) + 1800
        acc = {"window": name, "hours": 0, "ungated": 0, "gated": 0, "alerts": 0, "departures": 0,
               "incident_ids": set(), "multi": set(), "insufficient": set(), "reporting_only": set(), "physical_only": set(), "artifact_led": set()}
        while t < _t(b):
            out = replay.at(t)
            c = out["counts"]
            acc["hours"] += 1
            acc["ungated"] += c.get("candidates_ungated", 0)
            acc["gated"] += c.get("candidates", 0)
            acc["alerts"] += c.get("alerts", 0)
            acc["departures"] += c.get("departures", 0)
            for inc in out.get("incidents", []):
                departed = {s for s, st in inc["streams"].items() if st["departed"]}
                acc["incident_ids"].add(inc["id"])
                if departed & set(PHYSICAL) and departed & set(REPORTING):
                    acc["multi"].add(inc["id"])
                elif departed & set(REPORTING):
                    acc["reporting_only"].add(inc["id"])
                elif departed & set(PHYSICAL):
                    acc["physical_only"].add(inc["id"])
                if not inc["streams"]["navint"]["adequate"]:
                    acc["insufficient"].add(inc["id"])
                if inc["explanations"] and inc["explanations"][0]["id"] == "collection_artifact":
                    acc["artifact_led"].add(inc["id"])
            t += step_s
        rows.append({k: (len(v) if isinstance(v, set) else v) for k, v in acc.items()})
    return rows


def table(rows: list[dict]) -> str:
    cols = ["window", "hours", "ungated", "gated", "alerts", "departures", "incident_ids", "multi", "physical_only", "reporting_only", "insufficient", "artifact_led"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    line = " | ".join(c.ljust(widths[c]) for c in cols)
    out = [line, "-" * len(line)]
    for r in rows:
        out.append(" | ".join(str(r[c]).ljust(widths[c]) for c in cols))
    return "\n".join(out)


if __name__ == "__main__":
    from .replay import ReplayState
    scenario = sys.argv[1] if len(sys.argv) > 1 else "hormuz-2026-08-18"
    r = ReplayState(scenario)
    r.load()
    print(table(evaluate(r)))
