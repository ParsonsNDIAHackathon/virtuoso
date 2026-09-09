# Data sources — provenance, access, and limits

Team *Parsons of Interest*, NDIA Global Defense Hackathon, use case #5 Multi-INT Fusion Engine.
Everything below was verified on **2026-09-08** unless stated otherwise. Where two independent
efforts (this engine and the `ndia-hormuz-observatory` evidence notebook) reached the same
finding, that is noted, because agreement between them is itself evidence.

Access legend: **none** = no key or account · **free key** = free registration, key in `.env` ·
**free account** = registered account, S3-style keys · **paid** = not used.

## 1. Sources wired into the engine

| # | Stream | Provider | How obtained | Access | License / terms | Live cadence | Replay (Hormuz 2026-08-18) | Module |
|---|---|---|---|---|---|---|---|---|
| 1 | OSINT news events + entities | [GDELT 2.0](https://www.gdeltproject.org/data.html) `export` + `GKG` 15-min files | HTTPS download of `gdeltv2/lastupdate.txt`, then the export and GKG zips it names. Must use **https**; http returns 301 with an empty body. | none | Free, attribution requested | every 15 min | all 96 windows of the day, cached under `data/gdelt/`, filtered to bbox 23.5–28.5 N, 52–59 E or Hormuz/tanker keywords → 1,199 events, 151 conflict-coded | `ingest_gdelt.py`, `replay_gdelt.py` |
| 2 | Air tracks (live) | [adsb.lol](https://api.adsb.lol/docs) `/v2/mil`, `/v2/point/{lat}/{lon}/{nm}` | REST, JSON. Radius capped at 250 nm by the API. **Rate-limits bursts (429)**; requests spaced ≥ 3 s with backoff. | none | ODbL | every 60 s | n/a | `ingest_adsb.py` |
| 3 | Air tracks (historical) | adsb.lol `globe_history_2026` GitHub release `v2026.08.18-planes-readsb-prod-0` | Two split tar parts (~2 GB each) from GitHub Releases, parallel ranged download, resumable. Extractor streams the tar, keeps `traces/xx/trace_full_<hex>.json` with any position in the Gulf bbox. ~90 min download at ~0.7 MB/s, 57 s extraction. | none | ODbL | n/a | 1,534 aircraft in bbox (58 military) → `data/replay/2026-08-18_adsb.json` (63 MB) | `scripts/fetch_archive.py`, `replay_adsb.py` |
| 4a | Social posts (Telegram) | Telegram public channel previews `https://t.me/s/<channel>` | HTML scrape of the public preview page, paginated with `?before=<id>`. No login, no bot token. Geolocated with a Gulf gazetteer. | none | Telegram public content; only short text + link retained | every 5 min | `intelslava`, `Middle_East_Spectator` paged back to the day → 57 posts, 17 geolocated | `ingest_telegram.py` |
| 4b | Social posts (Reddit) | Reddit public JSON `https://www.reddit.com/r/<sub>/new.json` | Public listing, no key (descriptive User-Agent). Titles + selftext geolocated with the same gazetteer. | none | Reddit public content; title + link retained | every 5 min | paged back per sub | `ingest_reddit.py` |
| 4c | Social posts (Bluesky) | Bluesky public API `public.api.bsky.app/.../searchPosts` | Keyless public search (query, `sort=latest`, cursor paging). | none | Bluesky public content; text + link retained | every 5 min | paged back per query | `ingest_bluesky.py` |
| 4d | Social posts (Mastodon) | Mastodon public timeline `https://<instance>/api/v1/timelines/public` | Keyless public timeline (`max_id` paging); only geolocated posts kept. | none | Mastodon public content; text + link retained | every 5 min | paged back per instance | `ingest_mastodon.py` |
| 5 | Thermal anomalies | [NASA FIRMS](https://firms.modaps.eosdis.nasa.gov) VIIRS SNPP / NOAA-20 / NOAA-21 NRT, 375 m | CSV area API `api/area/csv/{MAP_KEY}/{SOURCE}/{bbox}/{days}/{date}` | **free key** `FIRMS_MAP_KEY` ([request](https://firms.modaps.eosdis.nasa.gov/api/map_key/)) | NASA open data | every 15 min, inside each drawn circle | day = 622 hotspots; novelty vs the previous two days = 23 new | `ingest_firms.py` |
| 6 | Radar ship detections | Copernicus Sentinel-1 GRD IW, `IW_GRDH_1S-COG` family, via [Copernicus Data Space](https://dataspace.copernicus.eu) | Catalog: OData `/odata/v1/Products` with `OData.CSC.Intersects` + `contains(Name,'GRDH')` (the STAC endpoint rejects collection `SENTINEL-1`). Download: S3 bucket `eodata`, endpoint `eodata.dataspace.copernicus.eu`, boto3 region `default`; VV COG tiff (~600 MB) + annotation XML. Detector works on COG overviews with tie-point geolocation, land mask, 500 m length cap. | **free account** `CDSE_S3_ACCESS_KEY` / `CDSE_S3_SECRET_KEY` (keys expire 2026-09-12) | Copernicus free and open | n/a (revisit is days) | 4 scenes: Aug 17 02:06Z (strait, 139 vessels in core), Aug 18 01:58Z (Gulf of Oman), Aug 18 14:31Z (southern Gulf, 1,609), Aug 20 14:16Z (strait, 292 in core) | `sar_ships.py` |
| 7 | Satellite basemap | [NASA GIBS](https://gibs.earthdata.nasa.gov) WMTS `VIIRS_SNPP_CorrectedReflectance_TrueColor` | Daily tiles by date, loaded directly by Leaflet | none | NASA open data | yesterday (daily imagery lags a few hours) | scenario day | `app/static/index.html` |
| 8 | Vessels (AIS, live) | [aisstream.io](https://aisstream.io) WebSocket | AOI bounding-box subscription; Class A/B position reports; exact circle filter; `/api/ais` | **free key** `AISSTREAM_API_KEY` | aisstream terms; volunteer receivers | on demand | none | `ingest_ais.py` |
| 9 | Graph store | Neo4j 2026.07.1 in Docker (`docker compose up -d`) | optional; in-memory networkx fallback with identical scoring | `NEO4J_PASSWORD` (local) | — | — | — | `store.py`, `neo4j_store.py` |

### Coverage limits you must state in the demo

- **Earlier aisstream probes received no messages in the Persian Gulf, Gulf of Oman, Arabian Sea, and Red Sea.**
  Probes of 12–15 s returned nothing; the eastern Med and Rotterdam return thousands. Found
  independently by both efforts. These were historical checks, not a current coverage guarantee. Live AIS is now connected to saved AOIs and the map; its source indicator reports current connection status.
- **No free historical AIS exists for the Gulf on Aug 18.** NOAA Marine Cadastre is US waters
  only; OpenSky and ADS-B Exchange history are gated; aisstream is live only. Commercial options
  (Datalastic, VesselFinder, MarineTraffic, Windward) require paid accounts; two inquiries were
  submitted from the observatory effort on Sep 8 with no reply. Any tanker-track layer that is not
  from a real feed must be labeled **SIMULATED**.
- **Sentinel-1 Aug 18 passes do not image the strait core.** Nearest strait-covering scenes are
  Aug 17 and Aug 20; the replay uses the nearest scene within 3 days and labels its age.
  The observatory's catalog query also lists strait-intersecting GRDH passes on **Aug 16 ~02:14Z**
  and **Aug 19 ~14:24Z** (not downloaded; ~2 GB each, or ~600 MB as VV COG) if a four-point
  before/after series is wanted. Sentinel-2 had no Aug 18 pass (Aug 15, 17 and 20 only).
- **FIRMS in the Gulf is dominated by gas flares.** Raw hotspot counts mean little; only
  novelty against a baseline is shown as a signal.
- **GDELT geocoding** is mostly city or country centroids. Country/state centroids (geo types
  1, 2, 5) are excluded from spatial correlation.
- **adsb.lol live** is cooperative ADS-B only; aircraft with transponders off are invisible.
  Military coverage in the Gulf depends on volunteer receivers.
- **Telegram** previews are only available for channels that enable them; several relevant
  channels (maritimesecurity, osinttechnical, Faytuks, IranIntl_En, sentdefender, UKMTO_official)
  are stale or preview-disabled. The observatory surfaced two more with previews, `iswnews`
  (origin of the attribution claim) and `eskannews_com`; add them to `DEFAULT_CHANNELS` in
  `ingest_telegram.py` if the demo wants them polled.

## 2. Sources evaluated and not used

| Source | Why not |
|---|---|
| X / Twitter API | Free tier has no search; Basic is 7-day only; full archive is Pro at ~$5k/month. Browser scraping declined. Three individual posts were manually reviewed in the observatory notebook and kept as paraphrased leads. |
| OpenSky Network history | Requires approved research account for historical queries. |
| NOAA Marine Cadastre AIS | US waters only. |
| Kaggle "Joint Staff J2 Analyst Correlation Review" (platform #106) | Needs a Kaggle login; not a live stream. NDIA metadata marks it **Internal Only** (observatory audit) — do not cite it in public deliverables without checking. |
| DroneRF (platform #73) | Lab RF captures with no geolocation; cannot be placed on a map honestly. |
| GDELT DOC API (`api/v2/doc/doc`) | Aggressive 429s; the 15-min file feed is used instead. |
| ACLED, OpenCelliD, Windward raw | Account gated or paid; not needed for the two-stream deliverable. |
| Sentinel-2 optical (Copernicus + Earth Search) | No Aug 18 pass. The observatory found a **keyless** route: the AWS `earth-search` STAC serves Sentinel-2 L2A cloud-optimized GeoTIFFs with HTTP range support, no account. Aug 17 tiles (~06:36Z) cover the eastern approaches/Gulf of Oman, not Larak; the Aug 20 Larak tile `S2B_40RDQ_20260820_0_L2A` (07:02Z) has ~38 % cloud; a thumbnail was inspected and **no ship was identified**. Crop script never ran (needs rasterio, no Python 3.14 wheel). |
| Global Fishing Watch APIs | Free key; identity, activity and radar-detection *derived* products, not a raw AIS archive. August coverage and rights unverified; not needed for the two-stream deliverable. |

## 3. Hackathon platform datasets

`GET /api/events/{slug}/datasets/` lists 47–50 entries (count differs by endpoint). **All are
link-only**: `download_file` returns 404 with `reason=link_only`, so every byte comes from the
upstream provider above. Team selections on the platform: GDELT 2.0 (#62), adsb.lol (#72),
DroneRF (#73). Full per-dataset HTTP audit: observatory `docs/data-access-audit.md`.

## 4. Curated (manual) evidence — from the observatory notebook

These are **analyst-reviewed records, not sensor data**, and are labeled as such wherever shown.

| File | Contents | Obtained |
|---|---|---|
| `data/hormuz-incident-seed.json` | 2 vessels (MINOAN DIGNITY IMO 9294484, bulk carrier; AMARA IMO 9333280, chemical/products tanker), 6 sources, 7 claims with `evidence_class`, `time_precision`, event vs publication time, `conflicts_with` | Manual review of public reporting and the IMO GISIS incident record, Sep 8 |
| `data/social-source-leads.json` | 3 reviewed leads (Unicanal on X, Spanish; ISWNews on Telegram, Persian; English X post on Aug 20 imagery) + 2 excluded with reasons; summaries are paraphrases, not translations | Manual review, Sep 8 |
| (observatory only) Natural Earth land polygons | the observatory notebook's offline basemap; **not used by this engine**, which masks land with `global-land-mask` | public domain |

## 5. Keys and secrets

All keys live in `fusion-engine/.env` (git-ignored; template in `.env.example`) and are read with
`os.environ` / `python-dotenv`. None are required to start the dashboard: GDELT, adsb.lol,
Telegram, and GIBS are keyless, so the two-stream demo runs with an empty `.env`.

| Variable | Needed for | Where to get it | Cost |
|---|---|---|---|
| `FIRMS_MAP_KEY` | thermal layer | firms.modaps.eosdis.nasa.gov/api/map_key | free |
| `AISSTREAM_API_KEY` | live AIS within saved AOIs | aisstream.io | free |
| `CDSE_S3_ACCESS_KEY`, `CDSE_S3_SECRET_KEY` | Sentinel-1 download | dataspace.copernicus.eu → S3 keys | free account; keys expire |
| `NEO4J_PASSWORD` | Neo4j container | choose locally | — |
| `FUSION_STORE` | `auto` \| `neo4j` \| `memory` | — | — |
| `FUSION_DATA_DIR` | move caches out of OneDrive | — | — |

Never commit `.env`. The observatory notebook used Windows DPAPI-encrypted key files loaded into
process env by `load-provider-keys.ps1`; that is a reasonable hardening step if the engine is
ever run on a shared machine.

## 6. Rebuilding the replay from scratch

```bash
python -m fusion.replay_gdelt 2026-08-18                                  # ~30 min, keyless
python scripts/fetch_archive.py 2026-08-18 C:/dev/ndia_raw/adsb_2026-08-18  # ~90 min, 4 GB
python -m fusion.replay_adsb extract C:/dev/ndia_raw/adsb_2026-08-18        # ~1 min
python -m fusion.ingest_social --since 2026-08-18 --until 2026-08-19         # seconds, all platforms
# or per platform: ingest_telegram / ingest_reddit / ingest_bluesky / ingest_mastodon
python -m fusion.ingest_firms 2026-08-18 --baseline 2026-08-16               # seconds, FIRMS key
python -m fusion.sar_ships <scene folder>                                    # per scene, CDSE keys
```

Raw archives (4 GB ADS-B, ~2.5 GB Sentinel-1) are kept outside the repo under `C:/dev/ndia_raw/`.

## 7. Live view vs. Hormuz replay — what each one actually pulls

The dashboard has two modes selected at the top of the page. They share the same map, the same
correlation rules, and the same alert ranking, but the data comes from different places.

| | **LIVE** | **REPLAY · Strait of Hormuz, 18 Aug 2026** |
|---|---|---|
| Time shown | now, refreshed continuously | any second of 2026-08-18 UTC, chosen with the scrubber |
| Where data comes from | the network, on a schedule | files on disk under `data/replay/`, built once from the archives in §6; no network calls except map tiles |
| Geography | worldwide military aircraft, plus everything inside the circles drawn on the map (defaults: Hormuz, DC, Kyiv, Levant, Taiwan, Baltic, 250 nm each) | the Gulf box 23.5–28.5 N, 52–59 E only |
| OSINT (GDELT) | the latest **2** 15-minute windows (about 30 min of world news), refreshed every 15 min | **all 96** windows of the day, filtered to the box or Hormuz/tanker keywords: 1,199 events |
| Aircraft (adsb.lol) | one API snapshot every **60 s**: all military-flagged aircraft worldwide + civil aircraft inside each circle. Only what receivers hear at that moment | the full-day archive: 1,534 aircraft that entered the box, every position they reported. At instant *t* the map shows each aircraft's last position at or before *t*, dropped after 5 min without a report, with 30-min tails |
| Social (all platforms) | Telegram channels + Reddit subs + Bluesky queries + Mastodon instances every **5 min** (`SOCIAL_PLATFORMS` to subset) | the same sources paged back to Aug 18 (`*_telegram.json` + `*_social.json` + per-platform files) |
| Thermal (FIRMS) | last 24 h inside each circle, refreshed every 15 min, novelty vs the previous 2 days | the day's 622 hotspots, novelty vs Aug 16–17: 23 new |
| Radar ships (Sentinel-1) | **none** (satellite revisit is days, so there is no "live" radar) | 4 real scenes; at instant *t* the nearest scene within 3 days is shown with its age labeled, plus the nearest strait-covering scene in amber |
| Vessels (AIS) | AOI-scoped aisstream.io positions when `AISSTREAM_API_KEY` is set; coverage varies | none (no free historical AIS) |
| Satellite basemap (GIBS) | yesterday's imagery, toggle | Aug 18 imagery, toggle |
| Correlation | events from the last 4 h vs aircraft within 75 km, re-scored after every pull | events from the prior 2 h vs aircraft positions as of *t*, re-scored for every instant |
| History strip | 15-minute bins over a selectable window (6 h to 7 days); GDELT and social (all platforms) backfilled 48 h from the sources inside the drawn circles, thermal from FIRMS, aircraft and correlations from this server's own persisted history | 96 fifteen-minute bins for the day |
| Graph store | Neo4j if running, else in-memory; persists across pulls | computed per instant, not persisted |

**Three things to say out loud in the demo:**

1. The Hormuz circle in LIVE mode shows the strait **today**, not Aug 18. The Aug 18 picture only
   exists in REPLAY.
2. Nothing in either mode is simulated or relocated. Every marker is a real report from the
   provider named in §1. Where a source has no coverage (AIS in the Gulf, live radar) the layer
   is absent rather than faked.
3. LIVE is a thin, wide slice (30 min of news, one aircraft snapshot per minute, the whole
   world). REPLAY is a deep, narrow slice (a full day, every aircraft position, one region).
   That is why replay has far more aircraft and events on screen than live does.
