# Multi-source Event Replay

This project has two deliberately separate layers:

- `app/sources/gdelt/prepare.py` downloads and filters GDELT 2.0 data.
- `app/replay/` replays any source that produces the generic timestamped JSONL format.

## Prepare GDELT Data

The default spatial filter is a 100 km radius around the Strait of Hormuz and uses `ActionGeo` only. All timestamps are UTC.

```bash
python -m app.sources.gdelt.prepare \
  --anchor 2026-09-07T12:00:00Z \
  --before-minutes 60 \
  --after-minutes 120 \
  --keywords shipping iran tanker \
  --keyword-mode matching \
  --output-dir data/gdelt
```

Use `--keyword-mode all` to retain every spatially matching event. The output contains `events.jsonl` and a `manifest.json`.

The convenience script uses these defaults:

- Current UTC time as the anchor
- 60 minutes before and after the anchor
- `shipping tanker iran` as keywords
- Keyword matching enabled
- Strait of Hormuz coordinates with a 100 km radius

```bash
./scripts/prepare_gdelt.sh
```

Override defaults with environment variables:

```bash
ANCHOR=2026-09-07T12:00:00Z \
BEFORE_MINUTES=120 \
AFTER_MINUTES=30 \
KEYWORDS="shipping vessel iran" \
KEYWORD_MODE=matching \
./scripts/prepare_gdelt.sh
```

Set `KEYWORD_MODE=all` to retain all events in the geographic and time window. `OUTPUT_DIR`, `LATITUDE`, `LONGITUDE`, `RADIUS_KM`, and `LOG_LEVEL` are also configurable when using the convenience script.

The preparer logs each phase, selected file count, filtering counts, elapsed time, and a rolling estimate of the remaining scan time. Use `--log-level DEBUG|INFO|WARNING|ERROR` when invoking the Python module; `INFO` is the default.

Keyword matches are metadata-only: the preparer searches structured Event fields, source URLs, and GKG metadata. It does not use the GDELT DOC API or download article bodies.

Each source must provide records shaped like this:

```json
{"timestamp":"2026-09-07T12:00:00Z","event_id":"id","payload":{}}
```

## Run Locally

```bash
uvicorn app.main:app --reload
```

The replay API is source-scoped:

```text
GET  /sources
GET  /stream/gdelt
POST /start/gdelt
POST /start/gdelt?speed=10
POST /stop/gdelt
POST /reset/gdelt
GET  /status/gdelt
```

`reset` moves the cursor to the first record and leaves playback stopped. `start` resumes playback. All subscribers to a source observe the same cursor.

## Build and Test the Replay Container

Prepare `data/gdelt/events.jsonl` first, then build and run:

```bash
./scripts/build.sh
./scripts/run.sh
```

The container remains attached to the terminal. Set `HOST_PORT` to use another local port, or `IMAGE_NAME` to use another image tag:

```bash
HOST_PORT=9000 IMAGE_NAME=my-replay ./scripts/run.sh
```

With the container running, test the service from another terminal:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/sources
curl -X POST http://localhost:8000/reset/gdelt
curl http://localhost:8000/status/gdelt
```

Start an SSE listener before starting playback:

```bash
curl -N http://localhost:8000/stream/gdelt
```

In a separate terminal, control playback:

```bash
curl -X POST 'http://localhost:8000/start/gdelt?speed=10'
curl -X POST http://localhost:8000/stop/gdelt
curl -X POST http://localhost:8000/reset/gdelt
```

`reset` returns to the first record without starting playback. `start` resumes from the current cursor, and `stop` pauses it.
