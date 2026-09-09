"""Pre-adjudicate a replay so the scrubber shows model verdicts.

The replay never calls the model on its own: at any instant it shows only verdicts already in
the cache. This walks the scenario at a fixed step, takes the best candidates at each instant
with the same ordering the live engine uses (pairs inside incident cells first, then shared
entities, then news-to-news), adjudicates them once, and leaves the verdicts in the cache.
Re-running is free: cached pairs are not sent again.

    python -m fusion.replay_adjudicate hormuz-2026-08-18 --from 2026-08-18T04:00 --to 2026-08-18T12:00 --step 60 --limit 12
"""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime, timezone

from .replay import ReplayState

log = logging.getLogger(__name__)


def _t(s: str) -> float:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", nargs="?", default="hormuz-2026-08-18")
    ap.add_argument("--from", dest="t_from", default=None, help="ISO UTC, default scenario start")
    ap.add_argument("--to", dest="t_to", default=None, help="ISO UTC, default scenario end")
    ap.add_argument("--step", type=int, default=60, help="minutes between instants")
    ap.add_argument("--limit", type=int, default=12, help="pairs adjudicated per instant")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    r = ReplayState(a.scenario)
    r.load()
    if not r.fusion_ai.available:
        raise SystemExit("OPENAI_API_KEY is not configured; nothing to adjudicate")
    t = _t(a.t_from) if a.t_from else r.t_min
    end = _t(a.t_to) if a.t_to else r.t_max
    t += 30 * 60 if not a.t_from else 0
    total = {"SUPPORTED": 0, "PLAUSIBLE": 0, "INSUFFICIENT_EVIDENCE": 0, "CONTRADICTED": 0}
    calls = cached = 0
    t0 = time.time()
    while t <= end:
        stamp = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%d %H:%MZ")
        results = r.adjudicate_instant(t, limit=a.limit)
        for res in results:
            total[res.verdict] = total.get(res.verdict, 0) + 1
            if res.cached:
                cached += 1
            else:
                calls += 1
        log.info("%s: %d pairs (%d new calls) -> %s", stamp, len(results), sum(1 for x in results if not x.cached),
                 {k: v for k, v in total.items() if v})
        t += a.step * 60
    log.info("done: %d model calls, %d cached, verdicts %s, %.0fs", calls, cached, total, time.time() - t0)


if __name__ == "__main__":
    main()
