import math

EARTH_KM = 6371.0088


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_KM * math.asin(math.sqrt(a))


def grid_key(lat, lon, cell_deg=1.0):
    return (int(math.floor(lat / cell_deg)), int(math.floor(lon / cell_deg)))


def neighbor_keys(lat, lon, cell_deg=1.0):
    r, c = grid_key(lat, lon, cell_deg)
    for dr in (-1, 0, 1):
        for dc in (-1, 0, 1):
            yield (r + dr, c + dc)
