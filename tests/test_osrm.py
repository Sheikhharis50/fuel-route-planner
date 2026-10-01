import polyline
import pytest
import requests
import responses
from django.core.cache import cache
from django.test import override_settings

from routing.errors import NoRouteError, RoutingError, RoutingUnavailable
from routing.services.osrm import get_route

START = (41.8781, -87.6298)
FINISH = (32.7767, -96.7970)
POINTS = [(41.8781, -87.6298), (37.0, -92.0), (32.7767, -96.7970)]
ROUTE_URL = 'https://osrm.test/route/v1/driving/-87.629800,41.878100;-96.797000,32.776700'


def ok_body():
    return {'code': 'Ok', 'routes': [{
        'distance': 1_500_000.0, 'duration': 50_000.0,
        'geometry': polyline.encode(POINTS, 6),
    }]}


@pytest.fixture(autouse=True)
def _settings():
    cache.clear()
    with override_settings(OSRM_BASE_URL='https://osrm.test', OSRM_USER_AGENT='test-agent/1.0',
                           OSRM_TIMEOUT_S=2):
        yield
    cache.clear()


@responses.activate
def test_single_call_full_geometry():
    responses.get(ROUTE_URL, json=ok_body())
    result = get_route(START, FINISH)
    assert len(responses.calls) == 1
    req = responses.calls[0].request
    assert 'overview=full' in req.url and 'geometries=polyline6' in req.url
    assert req.headers['User-Agent'] == 'test-agent/1.0'
    assert result.osrm_calls == 1 and result.cache_hit is False
    assert result.route.distance_m == 1_500_000.0
    assert result.route.duration_s == 50_000.0
    assert result.route.points == POINTS


@responses.activate
def test_cache_hit_makes_no_call():
    responses.get(ROUTE_URL, json=ok_body())
    first = get_route(START, FINISH)
    second = get_route(START, FINISH)
    assert len(responses.calls) == 1
    assert second.cache_hit is True and second.osrm_calls == 0
    assert second.route.points == first.route.points


@responses.activate
def test_base_url_override():
    responses.get('https://other.test/route/v1/driving/-87.629800,41.878100;-96.797000,32.776700',
                  json=ok_body())
    with override_settings(OSRM_BASE_URL='https://other.test'):
        get_route(START, FINISH)
    assert responses.calls[0].request.url.startswith('https://other.test/')


@responses.activate
@pytest.mark.parametrize('code', ['NoRoute', 'NoSegment'])
def test_no_route(code):
    responses.get(ROUTE_URL, json={'code': code, 'message': 'x'}, status=400)
    with pytest.raises(NoRouteError):
        get_route(START, FINISH)


@responses.activate
@pytest.mark.parametrize('status', [429, 500, 502, 503])
def test_unavailable_statuses(status):
    responses.get(ROUTE_URL, body='upstream secret error', status=status)
    with pytest.raises(RoutingUnavailable) as ei:
        get_route(START, FINISH)
    assert 'secret' not in ei.value.detail


@responses.activate
def test_timeout():
    responses.get(ROUTE_URL, body=requests.Timeout())
    with pytest.raises(RoutingUnavailable):
        get_route(START, FINISH)


@responses.activate
def test_connection_error():
    responses.get(ROUTE_URL, body=requests.ConnectionError())
    with pytest.raises(RoutingUnavailable):
        get_route(START, FINISH)


@responses.activate
def test_unexpected_response():
    responses.get(ROUTE_URL, json={'code': 'InvalidQuery', 'message': 'bad'}, status=400)
    with pytest.raises(RoutingError) as ei:
        get_route(START, FINISH)
    assert type(ei.value) is RoutingError


@responses.activate
def test_errors_not_cached():
    responses.get(ROUTE_URL, status=503)
    responses.get(ROUTE_URL, json=ok_body())
    with pytest.raises(RoutingUnavailable):
        get_route(START, FINISH)
    assert get_route(START, FINISH).cache_hit is False
