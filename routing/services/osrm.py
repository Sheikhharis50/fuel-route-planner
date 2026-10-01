import logging
import time
from dataclasses import dataclass

import polyline
import requests
from django.conf import settings
from django.core.cache import cache

from ..errors import NoRouteError, RoutingError, RoutingUnavailable

logger = logging.getLogger('routing.osrm')

_session = requests.Session()
_NO_ROUTE_CODES = {'NoRoute', 'NoSegment'}


@dataclass
class Route:
    distance_m: float
    duration_s: float
    points: list  # [(lat, lng), ...] full resolution


@dataclass
class RouteResult:
    route: Route
    cache_hit: bool
    osrm_calls: int
    osrm_latency_ms: float | None


def _cache_key(start, finish):
    return 'osrm:route:%.5f,%.5f;%.5f,%.5f' % (start[0], start[1], finish[0], finish[1])


def fetch_route(start, finish):
    """Single OSRM /route call. start/finish are (lat, lng)."""
    url = '%s/route/v1/driving/%.6f,%.6f;%.6f,%.6f' % (
        settings.OSRM_BASE_URL, start[1], start[0], finish[1], finish[0])
    params = {'overview': 'full', 'geometries': 'polyline6', 'steps': 'false', 'alternatives': 'false'}
    headers = {'User-Agent': settings.OSRM_USER_AGENT, 'Accept': 'application/json'}

    t0 = time.perf_counter()
    try:
        resp = _session.get(url, params=params, headers=headers, timeout=settings.OSRM_TIMEOUT_S)
    except requests.Timeout:
        latency = (time.perf_counter() - t0) * 1000
        logger.error('osrm timeout latency_ms=%.0f timeout_s=%s', latency, settings.OSRM_TIMEOUT_S)
        raise RoutingUnavailable('Routing service timed out.')
    except requests.RequestException as exc:
        latency = (time.perf_counter() - t0) * 1000
        logger.error('osrm connection error latency_ms=%.0f error=%s', latency, type(exc).__name__)
        raise RoutingUnavailable('Routing service is unreachable.')
    latency = (time.perf_counter() - t0) * 1000

    try:
        body = resp.json()
    except ValueError:
        body = {}
    osrm_code = body.get('code') if isinstance(body, dict) else None
    logger.info('osrm response status=%s code=%s latency_ms=%.0f bytes=%d',
                resp.status_code, osrm_code, latency, len(resp.content))

    if osrm_code in _NO_ROUTE_CODES:
        raise NoRouteError('No drivable route found between the given locations.', osrm_code=osrm_code)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise RoutingUnavailable('Routing service is unavailable or rate-limited; try again shortly.',
                                 upstream_status=resp.status_code)
    if resp.status_code != 200 or osrm_code != 'Ok' or not body.get('routes'):
        raise RoutingError('Routing service returned an unexpected response.',
                           upstream_status=resp.status_code)

    r = body['routes'][0]
    points = polyline.decode(r['geometry'], 6)
    route = Route(distance_m=float(r['distance']), duration_s=float(r['duration']), points=points)
    logger.info('osrm route distance_mi=%.1f duration_h=%.2f points=%d',
                route.distance_m / 1609.344, route.duration_s / 3600, len(points))
    return route, latency


def get_route(start, finish):
    key = _cache_key(start, finish)
    cached = cache.get(key)
    if cached is not None:
        logger.info('osrm cache hit key=%s', key)
        return RouteResult(route=cached, cache_hit=True, osrm_calls=0, osrm_latency_ms=None)
    route, latency = fetch_route(start, finish)
    cache.set(key, route, settings.ROUTE_CACHE_TTL_S)
    return RouteResult(route=route, cache_hit=False, osrm_calls=1, osrm_latency_ms=round(latency, 1))
