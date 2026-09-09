# Multi-INT Fusion Engine

**Team Parsons of Interest** · NDIA Global Defense Hackathon, Washington DC, 8–10 Sep 2026
**Use case #5: Multi-INT Fusion Engine** (Applied AI)

## The problem

Analysts watching a crisis region get many thin, noisy feeds: news wires in several languages, social media, aircraft transponders, satellite imagery, thermal sensors. Each one alone is ambiguous. The signal is in the overlap, and today a human has to find that overlap by hand. Use case #5 asks for a prototype that fuses unstructured OSINT with structured public sensor data, resolves the entities involved, and correlates them in space and time so hidden activity surfaces on its own.

## What we built

A working fusion engine and operator console that ingests six independent open data streams, links them, and ranks what matters.

- **Ingest.** News events, social posts, aircraft tracks, thermal anomalies, radar ship detections and satellite imagery arrive on their own schedules from public providers.
- **Resolve.** Actor, organization and place names are normalized across articles and languages so the same entity links everywhere it appears. Vessels are anchored on their IMO number.
- **Correlate.** Every significant news or social event is matched against aircraft nearby in space and time. Each match is scored by proximity, event severity and aircraft type (military, low altitude, emergency squawk). Independent corroboration raises the score; repeated copies of the same story do not.
- **Graph.** Events, actors, locations, aircraft and sources live in a Neo4j knowledge graph so relationships persist and can be queried.
- **Show.** One console: a map with layer toggles, a multi-source activity timeline, a ranked alert table with click-through to the original article or post, and a panel of analyst-reviewed incident records with full source provenance.

## The scenario

**Strait of Hormuz, 17–18 August 2026.** Public reporting describes an attack on the bulk carrier MINOAN DIGNITY on 17 August, with one crew fatality, and a separate detention of the tanker AMARA. Attribution, exact times and positions were contested in the open record. This is exactly the kind of problem the engine is for.

The console runs in two modes on the same map and rules. **Live** shows the world right now: the last half hour of news, an aircraft snapshot every minute, social and thermal feeds as they arrive, and the analyst can draw new areas of interest anywhere on the globe. **Replay** plays back the full 48 hours of 17–18 August from provider archives: every news window, every aircraft position reported in the Gulf, every post and hotspot, plus real radar imagery. An analyst scrubs to any second and the engine re-correlates for that instant.

The replay already surfaces two findings a human would have to dig for. A military-aircraft surge on the morning of 18 August precedes the social-media bursts and the press-reporting peak by several hours. Radar shows vessels holding in the strait roughly doubling between 17 and 20 August, consistent with shipping stacking up after the incident.

## Data sources

All free and public. Four of the six streams need no key at all, so the core demo runs anywhere.

| Stream | Provider | What it gives us |
|---|---|---|
| News events and entities | GDELT 2.0 | Geocoded, coded world news every 15 minutes, with the people, organizations and themes in each article |
| Aircraft tracks | adsb.lol | Live cooperative ADS-B with military flag; full daily archives for replay |
| Social posts | Telegram public channels | Four open-source-intelligence channels in English, Persian and Arabic, geolocated against a Gulf gazetteer |
| Thermal anomalies | NASA FIRMS (VIIRS) | Satellite fire and heat detections, filtered to what is new against a baseline |
| Radar ship detections | Copernicus Sentinel-1 | Our own ship detector run on real radar scenes of the strait, giving vessel counts where no AIS feed reaches |
| Satellite basemap | NASA GIBS | Daily true-color imagery for the day being viewed |
| Analyst-reviewed evidence | IMO incident register, INTERCARGO, Windward, trade press, vetted social posts | Seven sourced claims about the two vessels, each carrying its evidence class, time precision and publication date |

## Why it stands out

- **Real data, end to end.** Every marker is a genuine report from a named provider, and every alert clicks through to its source. The one illustrative layer, the RF spectrum sample, is labeled as such on the map and in the sources list.
- **Six streams, not two.** The use case asks for two disparate streams. We fuse news, social, air, thermal, radar and imagery, in three languages.
- **Time travel.** The replay mode turns a past incident into a live-feeling exercise, so the same engine trains analysts and supports operations.
- **Provenance built in.** Machine correlations and analyst-reviewed records are kept visibly distinct, each claim carries its source and publication date, and IMO-anchored vessel identity prevents false merges.
- **Deployable.** Python API, Neo4j and React console start with one Docker Compose command. Provider keys stay in a local ignored file. An in-memory fallback runs with identical results when no database is available.

## Team

Six team members split across ingestion, fusion and graph, the React console, containerization, and evidence curation. Code is at github.com/ParsonsNDIAHackathon/virtuoso.
