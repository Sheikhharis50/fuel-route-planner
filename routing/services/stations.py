import json
import logging
import threading
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from scipy.spatial import cKDTree

logger = logging.getLogger('routing.stations')

EARTH_RADIUS_MI = 3958.7613
_DENSIFY_MAX_SEG_MI = 0.5


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    address: str
    city: str
    state: str
    price: float
    lat: float
    lng: float


@dataclass(frozen=True)
class Candidate:
    station: Station
    mile: float
    offset_mi: float  # straight-line distance from the station point to the route


class StationIndex:
    def __init__(self, stations):
        self.stations = stations
        lat = np.array([s.lat for s in stations])
        lng = np.array([s.lng for s in stations])
        self.xyz = to_xyz(lat, lng)

    def __len__(self):
        return len(self.stations)

    @classmethod
    def from_file(cls, path):
        if not path.exists():
            raise RuntimeError(f'Stations file not found: {path}')
        raw = json.loads(path.read_text())
        stations = [Station(id=str(r['id']), name=r['name'].strip(), address=r['address'].strip(),
                            city=r['city'].strip(), state=r['state'].strip(), price=float(r['price']),
                            lat=float(r['lat']), lng=float(r['lng'])) for r in raw]
        logger.info('stations loaded count=%d path=%s', len(stations), path)
        return cls(stations)


def to_xyz(lat, lng):
    la, lo = np.radians(lat), np.radians(lng)
    return np.column_stack((np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)))


def haversine_mi(lat1, lng1, lat2, lng2):
    la1, lo1, la2, lo2 = map(np.radians, (lat1, lng1, lat2, lng2))
    a = np.sin((la2 - la1) / 2) ** 2 + np.cos(la1) * np.cos(la2) * np.sin((lo2 - lo1) / 2) ** 2
    return 2 * EARTH_RADIUS_MI * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def route_miles(points, total_distance_mi=None):
    """Return (lat, lng, cumulative_miles) arrays, densified so no segment exceeds 0.5 mi."""
    pts = np.asarray(points, dtype=float)
    if len(pts) < 2:
        return pts[:, 0], pts[:, 1], np.zeros(len(pts))
    seg = haversine_mi(pts[:-1, 0], pts[:-1, 1], pts[1:, 0], pts[1:, 1])
    n_sub = np.maximum(1, np.ceil(seg / _DENSIFY_MAX_SEG_MI).astype(int))
    if n_sub.max() > 1:
        seg_idx = np.repeat(np.arange(len(seg)), n_sub)
        offsets = np.arange(len(seg_idx)) - np.repeat(np.cumsum(n_sub) - n_sub, n_sub)
        t = offsets / n_sub[seg_idx]
        delta = pts[1:] - pts[:-1]
        dense = pts[seg_idx] + delta[seg_idx] * t[:, None]
        dense = np.vstack((dense, pts[-1:]))
        lat, lng = dense[:, 0], dense[:, 1]
        seg = np.repeat(seg / n_sub, n_sub)
    else:
        lat, lng = pts[:, 0], pts[:, 1]
    cum = np.concatenate(([0.0], np.cumsum(seg)))
    # Scale to the router's road distance so mile markers agree with the reported total.
    if total_distance_mi and cum[-1] > 0:
        cum *= total_distance_mi / cum[-1]
    return lat, lng, cum


def find_candidates(index, points, total_distance_mi, corridor_mi):
    lat, lng, cum = route_miles(points, total_distance_mi)
    tree = cKDTree(to_xyz(lat, lng))
    chord = 2 * np.sin(corridor_mi / EARTH_RADIUS_MI / 2)
    dist, idx = tree.query(index.xyz, k=1, distance_upper_bound=chord)
    hit = np.isfinite(dist)

    best = {}
    for si in np.nonzero(hit)[0]:
        route_i = int(idx[si])
        station = index.stations[si]
        offset = float(2 * EARTH_RADIUS_MI * np.arcsin(min(1.0, dist[si] / 2)))
        cur = best.get(route_i)
        # Stations at the same route position (same city centroid) collapse to the cheapest.
        if cur is None or (station.price, station.id) < (cur.station.price, cur.station.id):
            best[route_i] = Candidate(station=station, mile=float(cum[route_i]), offset_mi=offset)

    candidates = sorted(best.values(), key=lambda c: (c.mile, c.station.price))
    logger.info('corridor search route_points=%d densified=%d corridor_mi=%.1f in_corridor=%d candidates=%d',
                len(points), len(lat), corridor_mi, int(hit.sum()), len(candidates))
    return candidates


_index = None
_lock = threading.Lock()


def get_station_index():
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = StationIndex.from_file(settings.STATIONS_FILE)
    return _index
