# Multi-INT Fusion Engine — Team *Parsons of Interest*

NDIA Global Defense Hackathon, Main Event, Washington DC, 8–10 Sep 2026.
Use case **#5 Multi-INT Fusion Engine** (Applied AI category).

> Build a streaming data fusion prototype that correlates unstructured OSINT (news/social) with
> structured public network/RF data to automatically uncover hidden threats through entity
> resolution and temporal/spatial graphing.
> **Deliverable:** a UI dashboard demonstrating ingestion, correlation, and graphing of at least
> two disparate data streams.

## What this prototype does

| Stream | Source | Cadence | Role |
|---|---|---|---|
| OSINT events | [GDELT 2.0](https://www.gdeltproject.org/data.html) `export` + `GKG` | every 15 min | geocoded news events, CAMEO-coded, actors, persons, orgs, themes |
| Air tracks | [adsb.lol](https://api.adsb.lol/docs) `/v2/mil` + `/v2/point` | every 60 s | cooperative ADS-B, military flag, altitude, callsign |

Pipeline (`fusion/`):

1. **Ingest** — `ingest_gdelt.py` pulls the latest 15-minute window(s) and joins GKG entities to
   each event by source URL. `ingest_adsb.py` pulls all military-flagged aircraft worldwide plus
   250 nm circles around regions of interest.
2. **Entity resolution** — `correlate.resolve_actor()` normalizes actor/person/org strings
   (aliases, casing, punctuation) so the same actor links across articles and languages.
   Locations resolve on a 0.1° grid.
3. **Spatial/temporal correlation** — every event with severity ≥ 0.35 is matched against aircraft
   within 75 km and 4 h. Score = proximity × event severity (CAMEO root, Goldstein, tone,
   mentions) × aircraft weight (military, low altitude, emergency squawk).
4. **Knowledge graph** — Neo4j stores persistent `event / actor / location / aircraft /
   observation / source` nodes and `INVOLVES / LOCATED_AT / REPORTED_BY / NEAR / CO_LOCATED` edges.
5. **Dashboard** — FastAPI (`app/server.py`) serving a Leaflet map, a D3 force graph, and a ranked
   alert table with click-through to the source article.

## Run it

All commands run from this folder (`fusion-engine/`):

```bash
cd fusion-engine
cp .env.example .env  # set NEO4J_PASSWORD before starting the database
pip install -r requirements.txt
docker compose up -d
python -m fusion.pipeline            # one-shot: fetch, fuse, print top alerts, write data/snapshot.json
uvicorn app.server:app --port 8000   # dashboard at http://localhost:8000 (streams in background)
```

The Compose service runs Neo4j with named `neo4j_data` and `neo4j_logs` volumes. It binds Browser
(`7474`) and Bolt (`7687`) only to localhost. The application loads credentials from `.env`.
`docker compose down` preserves
the graph; `docker compose down -v` removes it. First ingestion takes a few minutes: two GDELT
windows (~6 MB GKG each) are downloaded and cached under `data/gdelt/`.

API: `/api/status`, `/api/alerts`, `/api/events?conflict_only=true`, `/api/aircraft`,
`/api/graph`, `/api/entity/{id}`, `POST /api/refresh`.

## Replay mode — Strait of Hormuz, 18 Aug 2026

Both replay layers are **real data for that day**, no relocation or synthetic positions:

| Layer | Source | How to build |
|---|---|---|
| OSINT | GDELT 2.0, all 96 windows of 2026-08-18, filtered to the Hormuz bbox (23.5–28.5 N, 52–59 E) or Hormuz/tanker keywords | `python -m fusion.replay_gdelt 2026-08-18` |
| Air tracks | adsb.lol `globe_history_2026` release `v2026.08.18-planes-readsb-prod-0` (two split tar parts, ~4 GB, ODbL) | download both parts to a folder, then `python -m fusion.replay_adsb extract <folder>` |

The extractor streams the tar, gunzips each `traces/xx/trace_full_<hex>.json`, keeps only aircraft with a
position inside the bbox, and writes `data/replay/2026-08-18_adsb.json` (small). At runtime
`fusion/replay.py` gives the fused picture at any instant: events from the prior 2 h, aircraft
positions as of that second, correlations, graph, and 30-minute track tails.

Dashboard: choose **REPLAY · Strait of Hormuz** in the mode selector, scrub or press Play.
API: `/api/replay/scenarios`, `/api/replay/{id}/config`, `/api/replay/{id}/timeline`,
`/api/replay/{id}/at?t=<epoch>`.

What is *not* available for free: historical AIS for the Gulf on that day (NOAA Marine Cadastre
is US waters only). If a tanker layer is added it must be labeled SIMULATED, or come from a live
feed such as aisstream.io labeled as current.

## Hackathon facts (from the platform API)

- Team UUID `979aa832-0234-40b6-abe6-16331abb76df`, lead Steve Dall (`sdall`), 6 members.
- Datasets selected on the platform: GDELT 2.0 (#62), adsb.lol ADS-B (#72), DroneRF (#73).
  All platform datasets are *link-only*; data comes from the upstream sources.
- **Project submissions due 2026-09-10 03:59 UTC** (Sep 9, 23:59 EDT). Judging 14:00 UTC Sep 10.
- Sibling service in this repo: `data-replay/` (Ruksana) — generic timestamped-JSONL replay with its own GDELT preparer.
- Judging weights: Mission Impact 30, Technical Innovation 25, Usability & Design 20,
  Security & Sustainability 15, Team Collaboration 10, Interoperability bonus ×0.1.

## Roadmap / ideas

- Add a third INT: DroneRF signatures or OpenCellID cell-tower density as the "RF/EM" layer;
  NOAA AIS for maritime.
- Neo4j is the persistent fusion graph. Each ADS-B pull creates timestamped `AirObservation`
  nodes, then materializes `NEAR` and `CO_LOCATED` relationships for that batch. Live and replay
  API projections query Neo4j directly.
- LLM summarization of each alert's corroborating articles (Claude API) into an analyst BLUF.
- Track history: persist ADS-B snapshots to detect loitering / orbit patterns, not just presence.
- Social stream (Telegram/X) ingest for true "social media spike" detection.
