"""Parallel, resumable download of an adsb.lol globe_history daily archive.

GitHub's release CDN throttles each connection to a few hundred KB/s, so a 4 GB day takes hours
with one stream. This splits each part into byte ranges fetched concurrently and resumes from
whatever is already on disk.

    python scripts/fetch_archive.py 2026-08-18 C:/dev/ndia_raw/adsb_2026-08-18 --threads 8
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

log = logging.getLogger("fetch")
CHUNK = 64 * 1024 * 1024          # 64 MB ranges


def release_assets(day: str) -> list[tuple[str, int]]:
    tag = f"v{day.replace('-', '.')}-planes-readsb-prod-0"
    year = day[:4]
    r = requests.get(f"https://api.github.com/repos/adsblol/globe_history_{year}/releases/tags/{tag}", timeout=30)
    r.raise_for_status()
    return [(a["browser_download_url"], a["size"]) for a in r.json()["assets"]]


def _fetch_range(url: str, path: Path, start: int, end: int, lock: threading.Lock, progress: dict) -> None:
    for attempt in range(6):
        try:
            with requests.get(url, headers={"Range": f"bytes={start}-{end}"}, stream=True, timeout=120) as r:
                if r.status_code != 206:
                    raise RuntimeError(f"expected 206, got {r.status_code}")
                pos = start
                with open(path, "r+b") as f:
                    f.seek(start)
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                        pos += len(chunk)
                        with lock:
                            progress["done"] += len(chunk)
                if pos != end + 1:
                    raise RuntimeError(f"short range {pos} != {end + 1}")
                return
        except Exception as e:
            log.warning("range %d-%d attempt %d failed: %s", start, end, attempt, e)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"range {start}-{end} failed permanently")


def fetch(url: str, size: int, dest: Path, threads: int, lock: threading.Lock, progress: dict) -> None:
    have = dest.stat().st_size if dest.exists() else 0
    if have >= size:
        log.info("%s already complete", dest.name)
        with lock:
            progress["done"] += size
        return
    # Pre-size the file so ranges can be written in place. Existing prefix bytes are kept.
    with open(dest, "ab") as f:
        f.truncate(size)
    with lock:
        progress["done"] += have
    ranges = [(s, min(s + CHUNK, size) - 1) for s in range(have, size, CHUNK)]
    log.info("%s: %d MB present, %d ranges of %d MB to fetch", dest.name, have // 2**20, len(ranges), CHUNK // 2**20)
    with ThreadPoolExecutor(threads) as ex:
        futs = [ex.submit(_fetch_range, url, dest, s, e, lock, progress) for s, e in ranges]
        for fu in as_completed(futs):
            fu.result()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("day")
    ap.add_argument("dest_dir")
    ap.add_argument("--threads", type=int, default=8)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    dest = Path(a.dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    assets = release_assets(a.day)
    total = sum(s for _, s in assets)
    progress = {"done": 0}
    lock = threading.Lock()
    t0 = time.time()
    stop = threading.Event()

    def reporter():
        while not stop.is_set():
            time.sleep(30)
            d = progress["done"]
            rate = d / max(time.time() - t0, 1)
            log.info("progress %.0f/%.0f MB (%.0f%%) %.1f MB/s", d / 2**20, total / 2**20, 100 * d / total, rate / 2**20)

    threading.Thread(target=reporter, daemon=True).start()
    try:
        for url, size in assets:
            fetch(url, size, dest / url.rsplit("/", 1)[-1], a.threads, lock, progress)
    finally:
        stop.set()
    log.info("ALL DONE in %.0fs", time.time() - t0)
    for p in sorted(dest.iterdir()):
        log.info("  %s %d bytes", p.name, p.stat().st_size)


if __name__ == "__main__":
    main()
