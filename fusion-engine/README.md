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
pip install -r requirements.txt
cp .env.example .env                 # then add your keys (see "Keys" below)
docker compose up --build             # command console at http://localhost:8080
```

`docker compose up` runs the React command console, FastAPI fusion API, and Neo4j on one private
network. Only the console is published; it proxies `/api/*` to FastAPI (long upstream timeouts, so
`POST /api/refresh` and the first replay build do not 504). `./data` is bind-mounted into the API
container as `/app/data` and Compose pins `FUSION_DATA_DIR=/app/data` / `FUSION_STORE=auto`, so any
dev-only values for those in `.env` are ignored inside the stack. The Neo4j browser is not published;
`docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD"` reaches it, or add a `ports:`
entry locally. For frontend development, run `uvicorn app.server:app --port 8000` from this
directory, then `npm ci && npm run dev` in `frontend/`; Vite proxies the same API routes to the local
FastAPI process.

**Fresh-box replay:** the first `REPLAY` request builds the day's GDELT cache into `./data/gdelt`
(about 10-15 minutes) and looks for `./data/replay/2026-08-18_adsb.json`. Produce that file once with
the archive steps under "Sources" (it is not in git) or the replay shows zero aircraft.

**Graph store selection** (`fusion/store.py`): at startup the engine probes Neo4j; if it answers, facts,
observations and correlations are persisted there (`fusion/neo4j_store.py`, Cypher correlation,
24 h retention on aircraft observations). If not, it falls back to the in-memory networkx engine
(`fusion/correlate_mem.py`) with identical scoring; `/api/status` reports `"store": "neo4j"|"memory"`.
Force one with `FUSION_STORE=neo4j|memory|auto`. `tests/parity_stores.py` checks both produce the
same alerts on one snapshot (last run: 568/568 identical).

**Keys** (`.env`, git-ignored): `NEO4J_PASSWORD` (any local password), `FIRMS_MAP_KEY`
(free, NASA FIRMS), `AISSTREAM_API_KEY` (free, aisstream.io), `CDSE_S3_ACCESS_KEY` /
`CDSE_S3_SECRET_KEY` (Copernicus Data Space S3 keys, for Sentinel-1). GDELT, adsb.lol, Telegram
previews and NASA GIBS need no key.

API: `/api/status`, `/api/alerts`, `/api/events?conflict_only=true`, `/api/aircraft`, `/api/firms`,
`/api/graph`, `/api/entity/{id}`, `/api/regions` (GET/POST/PATCH/DELETE), `POST /api/refresh`,
`/api/replay/scenarios`, `/api/replay/{id}/config|timeline|at?t=`.

## Sources

| Layer | Live | Replay (Hormuz 2026-08-18) | Module |
|---|---|---|---|
| OSINT events + GKG entities | GDELT 2.0, every 15 min | all 96 windows of the day, Hormuz bbox/keywords | `ingest_gdelt.py`, `replay_gdelt.py` |
| Air tracks | adsb.lol military feed + one query per drawn circle, every 60 s | adsb.lol `globe_history` daily archive (ODbL), traces filtered to the Gulf | `ingest_adsb.py`, `replay_adsb.py`, `scripts/fetch_archive.py` |
| Social posts | Telegram public channel previews, every 5 min | same channels paged back to the day | `ingest_telegram.py` |
| Thermal anomalies | NASA FIRMS VIIRS inside each circle, every 15 min, novelty vs 7 days earlier | FIRMS for the day, novelty vs Aug 11 | `ingest_firms.py` |
| Radar ship detections | n/a (revisit is days) | Sentinel-1 GRD COG scenes from Copernicus S3, nearest scene within 3 days, age labeled | `sar_ships.py` |
| Vessels (AIS) | aisstream.io (no coverage in the Gulf; works in the Med) | none free | `ingest_ais.py` |
| Satellite basemap | NASA GIBS VIIRS true color (yesterday) | same, scenario day | dashboard layer toggle |

## Replay mode — Strait of Hormuz, 18 Aug 2026

Both replay layers are **real data for that day**, no relocation or synthetic positions:

| Layer | Source | How to build |
|---|---|---|
| OSINT | GDELT 2.0, all 96 windows of 2026-08-18, filtered to the Hormuz bbox (23.5–28.5 N, 52–59 E) or Hormuz/tanker keywords | `python -m fusion.replay_gdelt 2026-08-18` |
| Air tracks | adsb.lol `globe_history_2026` release `v2026.08.18-planes-readsb-prod-0` (two split tar parts, ~4 GB, ODbL) | `python scripts/fetch_archive.py 2026-08-18 <folder>` then `python -m fusion.replay_adsb extract <folder>` |
| Social | Telegram public channels | `python -m fusion.ingest_telegram --since 2026-08-18 --until 2026-08-19` |
| Thermal | NASA FIRMS | `python -m fusion.ingest_firms 2026-08-18 --baseline 2026-08-11` |
| Radar ships | Sentinel-1 GRD COG (Copernicus S3) | download VV tiff + annotation XML for a scene, then `python -m fusion.sar_ships <scene folder>` |

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
