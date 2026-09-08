"""Sentinel-1 GRD ship detection (bright targets on dark sea) from Copernicus COG products.

Input (per scene, downloaded from CDSE S3 bucket `eodata`, product family IW_GRDH_1S-COG):
  <scene>/measurement__s1c-iw-grd-vv-...-cog.tiff   VV amplitude, uint16, cloud-optimized with overviews
  <scene>/annotation__s1c-iw-grd-vv-...-cog.xml     geolocationGrid tie points (line, pixel -> lat, lon)

Method (simple, honest, and fast enough for a hackathon):
  1. read an overview level of the COG (default level 2 ≈ 40 m pixels) with tifffile
  2. mask land with global-land-mask (1 km) evaluated through the tie-point geolocation
  3. adaptive threshold: pixel > k * local median (sea clutter) and > absolute floor
  4. connected components -> centroid, extent (m), peak/background ratio
Output: list of Detection with lat/lon, scene time, size estimate. Big targets (>150 m) are
tanker/bulk-class; the strait's traffic separation lanes should light up.

    python -m fusion.sar_ships C:/dev/ndia_raw/s1/<SCENE>_COG.SAFE --level 2
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


@dataclass
class Detection:
    id: str
    ts: str                 # scene sensing time (UTC)
    lat: float
    lon: float
    length_m: float         # max extent of the blob in metres (rough)
    area_px: int
    contrast: float         # peak / local background
    scene: str
    source: str = "Sentinel-1 GRD VV (Copernicus, CC-BY)"

    def to_dict(self):
        return asdict(self)


def read_geogrid(xml_path: Path):
    """Return (lines, pixels, lats, lons) arrays from the annotation geolocationGrid."""
    root = ET.parse(xml_path).getroot()
    pts = root.findall(".//geolocationGridPoint")
    L = np.array([int(p.find("line").text) for p in pts])
    P = np.array([int(p.find("pixel").text) for p in pts])
    LA = np.array([float(p.find("latitude").text) for p in pts])
    LO = np.array([float(p.find("longitude").text) for p in pts])
    n_lines = int(root.find(".//imageInformation/numberOfLines").text)
    n_px = int(root.find(".//imageInformation/numberOfSamples").text)
    t0 = root.find(".//imageInformation/productFirstLineUtcTime").text
    spacing = float(root.find(".//imageInformation/rangePixelSpacing").text)
    return L, P, LA, LO, n_lines, n_px, t0, spacing


def make_interp(L, P, LA, LO):
    from scipy.interpolate import LinearNDInterpolator
    pts = np.column_stack([L, P])
    return LinearNDInterpolator(pts, LA), LinearNDInterpolator(pts, LO)


def read_overview(tiff_path: Path, level: int):
    import tifffile
    with tifffile.TiffFile(tiff_path) as tf:
        s = tf.series[0]
        lv = s.levels[min(level, len(s.levels) - 1)]
        arr = lv.asarray()
        full_h, full_w = s.shape[-2], s.shape[-1]
    return np.asarray(arr).squeeze(), full_h, full_w


def detect(scene_dir: Path, level: int = 2, k: float = 5.0, floor: float | None = None,
           win: int = 41, min_area: int = 2, bbox=None) -> list[Detection]:
    from scipy import ndimage
    from global_land_mask import globe

    scene_dir = Path(scene_dir)
    tiff = next(p for p in scene_dir.iterdir() if "-vv-" in p.name and p.suffix in (".tiff", ".tif"))
    xml = next(p for p in scene_dir.iterdir() if "-vv-" in p.name and p.suffix == ".xml")
    L, P, LA, LO, n_lines, n_px, t0, spacing = read_geogrid(xml)
    lat_i, lon_i = make_interp(L, P, LA, LO)
    img, full_h, full_w = read_overview(tiff, level)
    sy, sx = full_h / img.shape[0], full_w / img.shape[1]
    px_m = spacing * sx
    log.info("%s: overview %s (%.0f m px), full %dx%d", scene_dir.name[:32], img.shape, px_m, full_h, full_w)

    # geolocate every overview pixel (coarse grid then land mask)
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    lat = lat_i(yy * sy, xx * sx)
    lon = lon_i(yy * sy, xx * sx)
    valid = ~np.isnan(lat)
    sea = np.zeros_like(valid)
    sea[valid] = ~globe.is_land(lat[valid], lon[valid])
    if bbox:
        la0, lo0, la1, lo1 = bbox
        sea &= (lat >= la0) & (lat <= la1) & (lon >= lo0) & (lon <= lo1)
    # dilate land slightly so coastlines/harbour walls don't trigger
    land = ~sea
    land = ndimage.binary_dilation(land, iterations=3)
    sea = ~land & valid

    a = img.astype(np.float32)
    a[~sea] = np.nan
    bg = ndimage.generic_filter(np.nan_to_num(a, nan=0.0), np.median, size=win) if img.size < 4e6 else \
        ndimage.median_filter(np.nan_to_num(a, nan=0.0), size=win)
    if floor is None:
        floor = float(np.nanpercentile(a, 99.0))
    cand = sea & (a > k * np.maximum(bg, 1.0)) & (a > floor)
    lab, n = ndimage.label(cand)
    dets = []
    if n:
        idx = np.arange(1, n + 1)
        areas = ndimage.sum(cand, lab, idx)
        cents = ndimage.center_of_mass(cand, lab, idx)
        maxs = ndimage.maximum(a, lab, idx)
        slices = ndimage.find_objects(lab)
        ts = datetime.fromisoformat(t0.replace("Z", "")).replace(tzinfo=timezone.utc).isoformat()
        for i, (ar, (cy, cx), mx, sl) in enumerate(zip(areas, cents, maxs, slices)):
            if ar < min_area:
                continue
            h = sl[0].stop - sl[0].start
            w = sl[1].stop - sl[1].start
            la = float(lat_i(cy * sy, cx * sx)); lo = float(lon_i(cy * sy, cx * sx))
            if np.isnan(la):
                continue
            b = float(bg[int(cy), int(cx)]) or 1.0
            dets.append(Detection(
                id=f"sar:{scene_dir.name[17:32]}:{i}", ts=ts, lat=round(la, 5), lon=round(lo, 5),
                length_m=round(max(h, w) * px_m, 0), area_px=int(ar), contrast=round(float(mx) / b, 1),
                scene=scene_dir.name,
            ))
    log.info("%d detections (k=%.1f floor=%.0f)", len(dets), k, floor)
    return dets


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("scene_dir")
    ap.add_argument("--level", type=int, default=2)
    ap.add_argument("--k", type=float, default=5.0)
    ap.add_argument("--bbox", default="23.5,52,28.5,59")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    bbox = tuple(float(x) for x in a.bbox.split(","))
    dets = detect(Path(a.scene_dir), level=a.level, k=a.k, bbox=bbox)
    root = Path(__file__).resolve().parent.parent
    day = re.search(r"(\d{8})T", Path(a.scene_dir).name).group(1)
    out = Path(a.out) if a.out else root / "data" / "replay" / f"{day[:4]}-{day[4:6]}-{day[6:]}_sar.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    prev = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    prev = [d for d in prev if d.get("scene") != Path(a.scene_dir).name]
    out.write_text(json.dumps(prev + [d.to_dict() for d in dets]), encoding="utf-8")
    big = sorted(dets, key=lambda d: -d.length_m)[:12]
    print(f"{len(dets)} detections -> {out}")
    for d in big:
        print(f"  {d.lat:.3f},{d.lon:.3f} ~{d.length_m:.0f} m contrast {d.contrast}")
