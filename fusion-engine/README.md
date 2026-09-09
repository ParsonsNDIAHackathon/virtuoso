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
| Social posts | Telegram public channel previews | every 5 min | post text, channel, named/explicit location, keywords |
| Air tracks | [adsb.lol](https://api.adsb.lol/docs) `/v2/mil` + `/v2/point` | every 60 s | cooperative ADS-B, military flag, altitude, callsign |
| Thermal observations | NASA FIRMS VIIRS | every 15 min | acquisition-time hotspots, scored against a two-day baseline |

Pipeline (`fusion/`):

1. **Ingest** — `ingest_gdelt.py` pulls the latest 15-minute window(s) and joins GKG entities to
   each event by source URL. `ingest_adsb.py` pulls all military-flagged aircraft worldwide plus
   250 nm circles around regions of interest.
2. **Candidate retrieval** — `fusion_ai.generate_candidates()` uses a different permissive time and
   distance window for each source pair. It suppresses routine FIRMS pixels and ordinary high-altitude
   civil traffic, adds shared source entities/themes, uses a spatial index, and keeps a bounded top set.
   These are explicitly cues, not findings. The older OSINT/ADS-B proximity score remains visible as a
   dashed heuristic link for comparison and no longer receives a fake same-cell “corroboration” boost.
3. **OpenAI evidence adjudication** — one structured Responses API prompt classifies each pair as
   `SUPPORTED`, `PLAUSIBLE` (needs review), `INSUFFICIENT_EVIDENCE`, or `CONTRADICTED`. The prompt may
   use only the two source records and asserted one-hop graph facts. When a GDELT record reaches
   adjudication, the engine retrieves and caches its source article text and includes up to
   `FUSION_SOURCE_DOC_MAX_CHARS`; outside knowledge and unstated
   aircraft/operator attribution are prohibited. Verdict, relation, strength, rationale, limitation,
   supporting facts, and explicit entity resolutions are cached in local SQLite and written to Neo4j.
4. **Multi-source clustering** — positive assessment edges form connected evidence clusters ranked by
   distinct modalities and evidence strength. OpenAI produces a cached analyst BLUF for the top cluster.
5. **Knowledge graph and dashboard** — Neo4j stores source records, retrieval candidates, LLM
   assessments, resolved entities, and clusters. The map supports selecting any two individual GDELT,
   Telegram, ADS-B, AIS, or FIRMS markers and invoking the same adjudicator on demand. Rejected assessments
   are persisted but hidden unless **Show rejected** is enabled.

Two GDELT records can come from the same article while describing different incidents. Comparisons
show **Same article · Confirmed** for matching URLs (tracking parameters removed, successful redirects
resolved), separately from the model's **same / related / unrelated / uncertain** incident assessment.
Shared article matches remain visible even when the incident link has insufficient evidence. They
count as one reporting source; repeated event records do not increase the cluster's corroboration
weight. Different URLs alone do not establish independent reporting.

The comparison result shows article-text availability, character counts, and truncation. Use
**Reanalyze with fresh source text** to fetch the source again and replace the cached verdict; this
makes another model request. Failed article retrievals expire after five minutes. Prompt-versioned
caches keep older assessments from being reused by the updated adjudicator.

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

**Fresh-box replay:** replay requests never download or build data. Run
`scripts/build_replay_data.py` as described below before the demo. Missing layer files are reported in
the replay configuration and simply render as empty rather than blocking the app.

**Graph store selection** (`fusion/store.py`): at startup the engine probes Neo4j; if it answers, facts,
observations and correlations are persisted there (`fusion/neo4j_store.py`, Cypher correlation,
24 h retention on aircraft observations). If not, it falls back to the in-memory networkx engine
(`fusion/correlate_mem.py`) with identical scoring; `/api/status` reports `"store": "neo4j"|"memory"`.
Force one with `FUSION_STORE=neo4j|memory|auto`. `tests/parity_stores.py` checks both produce the
same alerts on one snapshot (last run: 568/568 identical).

**Keys** (`.env`, git-ignored): `NEO4J_PASSWORD` (any local password), `OPENAI_API_KEY`
(leave blank to disable LLM calls while retaining candidate generation), `FIRMS_MAP_KEY`
(free, NASA FIRMS), `AISSTREAM_API_KEY` (free, aisstream.io), `CDSE_S3_ACCESS_KEY` /
`CDSE_S3_SECRET_KEY` (Copernicus Data Space S3 keys, for Sentinel-1). GDELT, adsb.lol, Telegram
previews and NASA GIBS need no key.

**Test AIS and AI together:** run `docker compose up -d --build api frontend`, then hard-refresh
`http://localhost:8080` (or your configured `FUSION_PORT`). The live source legend should show
both **AIS · VESSELS** and **OPENAI · ADJUDICATION**. Enable **Compare with AI**, expand any
clusters, select two individual GDELT, Telegram, ADS-B, AIS, or FIRMS markers, then click
**Adjudicate evidence**. The result separates article identity from incident association and
explains the supporting facts and limitations. Repeat in a replay: selecting the first marker
pauses playback and binds the comparison to that displayed instant. AIS comparisons are live-only.
The legacy ADS-B/OSINT cues panel is removed; map zoom controls are at the bottom-right.

Regression checks (with `pytest` installed): `python -m pytest tests -q`.

API: `/api/status`, `/api/alerts`, `/api/events?conflict_only=true`, `/api/aircraft`, `/api/firms`,
`/api/ais`, `/api/graph`, `/api/entity/{id}`, `/api/regions` (GET/POST/PATCH/DELETE), `POST /api/refresh`,
`/api/fusion/status|candidates|assessments|clusters`, `POST /api/fusion/adjudicate`,
`/api/replay/scenarios`, `/api/replay/{id}/config|timeline|at?t=`.

## Sources

| Layer | Live | Replay (Hormuz 2026-08-18) | Module |
|---|---|---|---|
| OSINT events + GKG entities | GDELT 2.0, every 15 min | all 96 windows of the day, Hormuz bbox/keywords | `ingest_gdelt.py`, `replay_gdelt.py` |
| Air tracks | adsb.lol military feed + one query per drawn circle, every 60 s | adsb.lol `globe_history` daily archive (ODbL), traces filtered to the Gulf | `ingest_adsb.py`, `replay_adsb.py`, `scripts/fetch_archive.py` |
| Social posts | Telegram public channel previews, every 5 min | same channels paged back to the day | `ingest_telegram.py` |
| Thermal anomalies | NASA FIRMS VIIRS inside each circle, every 15 min, novelty vs 7 days earlier | FIRMS for the day, novelty vs Aug 11 | `ingest_firms.py` |
| Radar ship detections | n/a (revisit is days) | Sentinel-1 GRD COG scenes from Copernicus S3, nearest scene within 3 days, age labeled | `sar_ships.py` |
| Vessels (AIS) | aisstream.io · AOI bounding boxes, then circle filtering; coverage varies | none free | `ingest_ais.py` |
| Satellite basemap | NASA GIBS VIIRS true color (yesterday) | same, scenario day | dashboard layer toggle |

## Replay mode — Strait of Hormuz, 18 Aug 2026

Build all requested replay layers outside the API process with the standalone, restartable builder:

```bash
# Builds missing keyless layers; FIRMS needs FIRMS_MAP_KEY. ADS-B records an actionable error
# unless an existing archive is supplied or the large download is explicitly requested.
./build-replay-data.sh
./build-replay-data.sh 2026-08-17 2026-08-18 --adsb-archive /path/to/archives
# Or opt into downloading the multi-GB daily ADS-B archives:
./build-replay-data.sh 2026-08-17 2026-08-18 --download-adsb /tmp/adsb-archives
```

It writes one JSON file per day/source plus a manifest with counts, hashes, and layer errors under
`data/replay/`. Existing files are not rebuilt unless `--force` is supplied. The replay scrubber never
calls OpenAI; it reads cached verdicts. Selecting two markers can still run an explicit on-demand call.

All replay layers are **real data for that day**, with no relocation or synthetic positions:

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

- Team UUID `979aa832-0234-40b6-abe6-16331abb76df`, lead (Ruksana), 6 members.
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
- Track history: persist ADS-B snapshots to detect loitering / orbit patterns, not just presence.
- Extend entity extraction beyond the current source fields and LLM-resolved explicit mentions.

### Live AIS

Set `AISSTREAM_API_KEY` in `fusion-engine/.env` and restart the API service. In Live mode,
add an area of interest and enable **AIS**. Green markers show vessel positions; clicking
one shows MMSI, name, speed, course, heading, report time, and position age. Enable
**Compare with AI** to compare a vessel against another vessel, news item, aircraft, Telegram
post, or thermal anomaly. The display
refreshes every 10 seconds. The AIS source indicator reports missing credentials,
connection failures, and the current vessel count.

One backend WebSocket subscribes to the union of bounding boxes enclosing saved AOI
circles, splitting boxes at the dateline. Incoming reports are filtered against the circles;
map panning and the optional “Filter view to AOIs” checkbox never widen AIS coverage.
Adding/removing AOIs reconnects with the updated subscription and immediately drops
positions outside the remaining circles. With no AOIs, no AIS subscription is opened.
The layer toggle controls display and browser polling; it does not stop the shared server feed.

Only latest positions are kept in memory. Reports older than 15 minutes are labeled stale
in vessel details and removed after 60 minutes. A reporting gap can reflect receiver coverage
or connectivity; it is not evidence of intentional AIS shutdown. AIS has no replay support.
Provider protocol: https://aisstream.io/documentation .

The live Activity Timeline records AOI-scoped AIS vessel counts once per fusion cycle
(approximately 60 seconds) and averages available samples into 15-minute bins. Samples
persist in `data/history.jsonl`. Missing credentials, disconnected feeds, and no AOIs produce
gaps, not zero counts. Older history has no AIS values; no historical AIS is backfilled.
Counts reflect the AOIs active at recording time and use the same latest-position expiry
as the map. The timeline continues recording when the AIS map layer is hidden.

### AI result updates

Live AIS joins cross-source candidate retrieval with news, Telegram, ADS-B, and FIRMS.
Proximity retrieves candidates; the AI still requires supporting source facts before asserting
an association. Vessel identity, ownership, intent, and deliberate transmitter shutdown are
not inferred from positions or coverage gaps.

Automatic verdicts appear as each pair completes and survive live refreshes. The latest assessment
per record pair is retained for up to an hour while both records remain available, bounded to 500
pairs. Every new verdict includes the actual observation timestamps and positions; retained results
do not become cached verdicts for newer positions. The map draws assessed links at their evaluated
positions. **AI ASSESSMENTS** counts all returned verdicts; **Show rejected** reveals unsupported
or contradicted links. The count can be positive even when no association is supported.

Graph artifact writes run in a single background worker so database delays cannot block ingestion
or automatic analysis. Queries default to a 20-second transaction timeout. Article retrieval has a
12-second overall budget per article, falling back to the supplied record fields with an explicit
evidence gap. Manual requests return an error after 90 seconds instead of spinning indefinitely;
the browser also has a 100-second timeout. Timeouts do not guarantee cancellation of an already
running provider call.
