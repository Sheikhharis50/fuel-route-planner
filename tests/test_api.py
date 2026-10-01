import json
import re

import numpy as np
import polyline
import pytest
import requests
import responses
from django.core.cache import cache
from django.test import override_settings

from routing.services.geocoding import resolve
from routing.services.stations import haversine_mi

ROUTE_RE = re.compile(r'https://osrm\.test/route/v1/driving/.*')
URL = '/api/route'


def straight_route(a, b, n=2000, road_factor=1.0):
    pts = [(float(a.lat + (b.lat - a.lat) * t), float(a.lng + (b.lng - a.lng) * t)) for t in np.linspace(0, 1, n)]
    dist_mi = float(haversine_mi(a.lat, a.lng, b.lat, b.lng)) * road_factor
    return {'code': 'Ok', 'routes': [{'distance': dist_mi * 1609.344, 'duration': dist_mi * 60,
                                      'geometry': polyline.encode(pts, 6)}]}


@pytest.fixture(autouse=True)
def _settings():
    cache.clear()
    with override_settings(OSRM_BASE_URL='https://osrm.test', FUEL_CORRIDOR_MILES=10):
        yield
    cache.clear()


@pytest.fixture
def mocked():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
        yield rsps


def mock_route(rsps, start, finish, **kw):
    rsps.add(responses.GET, ROUTE_RE, json=straight_route(resolve(start), resolve(finish), **kw))


def get(client, start, finish, **headers):
    return client.get(URL, {'start': start, 'finish': finish}, **headers)


def test_long_trip_geojson_contract(client, mocked):
    mock_route(mocked, 'Chicago, IL', 'Dallas, TX')
    r = get(client, 'Chicago, IL', 'Dallas, TX')
    assert r.status_code == 200 and r['Content-Type'].startswith('application/json')
    body = r.json()
    assert body['type'] == 'FeatureCollection'
    kinds = [f['properties']['kind'] for f in body['features']]
    assert kinds[0] == 'route' and kinds[1] == 'start' and kinds[-1] == 'finish'
    stops = [f for f in body['features'] if f['properties']['kind'] == 'fuel_stop']
    assert len(stops) >= 1
    miles = [f['properties']['mile_marker'] for f in stops]
    assert miles == sorted(miles)
    for f in body['features']:
        g = f['geometry']
        coords = [g['coordinates']] if g['type'] == 'Point' else g['coordinates']
        for lng, lat in coords:
            assert -125 < lng < -66 and 24 < lat < 50
    for f in stops:
        p = f['properties']
        for key in ('station_id', 'name', 'address', 'city', 'state', 'price_per_gallon', 'mile_marker',
                    'gallons', 'cost'):
            assert key in p
    route = body['features'][0]
    assert route['geometry']['type'] == 'LineString'
    assert {'distance_miles', 'duration_seconds'} <= route['properties'].keys()
    json.dumps(body)


def test_summary_consistent(client, mocked):
    mock_route(mocked, 'Chicago, IL', 'Dallas, TX')
    body = get(client, 'Chicago, IL', 'Dallas, TX').json()
    s = body['summary']
    stops = [f['properties'] for f in body['features'] if f['properties']['kind'] == 'fuel_stop']
    assert s['number_of_stops'] == len(stops)
    assert s['total_fuel_cost'] == pytest.approx(round(sum(p['cost'] for p in stops), 2), abs=1e-9)
    assert s['total_gallons_purchased'] == pytest.approx(sum(p['gallons'] for p in stops), abs=0.01)
    assert s['vehicle'] == {'range_miles': 500.0, 'mpg': 10.0, 'tank_gallons': 50.0, 'starting_fuel_gallons': 50.0}
    for key in ('total_distance_miles', 'total_duration_seconds', 'total_gallons_consumed'):
        assert key in s


def test_meta_call_budget_and_cache(client, mocked):
    mock_route(mocked, 'Chicago, IL', 'Dallas, TX')
    m1 = get(client, 'Chicago, IL', 'Dallas, TX').json()['meta']
    m2 = get(client, 'chicago, il', 'Dallas, TX').json()['meta']
    assert len(mocked.calls) == 1
    assert (m1['osrm_calls'], m1['cache_hit']) == (1, False)
    assert (m2['osrm_calls'], m2['cache_hit']) == (0, True)
    for key in ('resolve', 'route', 'corridor', 'optimize', 'serialize', 'total'):
        assert key in m1['timings_ms']
    assert m1['candidate_stations'] > 0 and m1['request_id']
    assert m1['route_points_returned'] <= m1['route_points_full']


def test_short_trip_zero_cost(client, mocked):
    mock_route(mocked, 'Chicago, IL', 'Milwaukee, WI', road_factor=1.1)
    body = get(client, 'Chicago, IL', 'Milwaukee, WI').json()
    assert body['summary']['number_of_stops'] == 0
    assert body['summary']['total_fuel_cost'] == 0


def test_request_id_round_trip_and_logged(client, mocked, caplog):
    mock_route(mocked, 'Chicago, IL', 'Dallas, TX')
    caplog.clear()
    with caplog.at_level('INFO', logger='routing'):
        r = get(client, 'Chicago, IL', 'Dallas, TX', HTTP_X_REQUEST_ID='abc-123')
    assert r['X-Request-ID'] == 'abc-123'
    assert r.json()['meta']['request_id'] == 'abc-123'
    routing_records = [rec for rec in caplog.records if rec.name.startswith('routing')]
    bad = [(rec.name, rec.request_id, rec.getMessage()[:60]) for rec in routing_records if rec.request_id != 'abc-123']
    assert routing_records and not bad, bad
    messages = ' '.join(rec.getMessage() for rec in routing_records)
    for needle in ('route request', 'resolved', 'osrm response', 'corridor search', 'fuel stop', 'fuel plan',
                   'route response'):
        assert needle in messages


def test_unsafe_request_id_replaced(client, mocked):
    mock_route(mocked, 'Chicago, IL', 'Dallas, TX')
    r = get(client, 'Chicago, IL', 'Dallas, TX', HTTP_X_REQUEST_ID='bad id\nwith newline')
    assert r['X-Request-ID'] != 'bad id\nwith newline' and len(r['X-Request-ID']) == 32


@pytest.mark.parametrize('params,missing', [({'finish': 'Dallas, TX'}, 'start'), ({'start': 'Chicago, IL'}, 'finish'),
                                            ({'start': '', 'finish': 'Dallas, TX'}, 'start')])
def test_missing_parameter(client, params, missing):
    r = client.get(URL, params)
    assert r.status_code == 400
    body = r.json()
    assert body['code'] == 'invalid_request' and missing in body['detail']


def test_unknown_city(client):
    r = get(client, 'Nowheresville, KS', 'Dallas, TX')
    assert r.status_code == 400
    assert r.json()['code'] == 'location_not_found' and r.json()['field'] == 'start'


def test_non_us_location(client):
    r = get(client, 'Chicago, IL', 'Toronto, ON')
    assert r.status_code == 400 and r.json()['code'] == 'unsupported_location'


def test_malformed_location(client):
    r = get(client, 'Chicago', 'Dallas, TX')
    assert r.status_code == 400 and r.json()['code'] == 'invalid_location_format'


def test_infeasible_route(client, mocked):
    mock_route(mocked, 'Chicago, IL', 'Dallas, TX')
    with override_settings(FUEL_CORRIDOR_MILES=0.001):
        r = get(client, 'Chicago, IL', 'Dallas, TX')
    assert r.status_code == 422
    body = r.json()
    assert body['code'] == 'route_infeasible' and 'gap_start_mile' in body


def test_no_route(client, mocked):
    mocked.add(responses.GET, ROUTE_RE, json={'code': 'NoRoute'}, status=400)
    r = get(client, 'Chicago, IL', 'Dallas, TX')
    assert r.status_code == 422 and r.json()['code'] == 'no_route'


def test_routing_timeout(client, mocked):
    mocked.add(responses.GET, ROUTE_RE, body=requests.Timeout())
    r = get(client, 'Chicago, IL', 'Dallas, TX')
    assert r.status_code == 503 and r.json()['code'] == 'routing_unavailable'


def test_routing_unexpected_error(client, mocked):
    mocked.add(responses.GET, ROUTE_RE, json={'code': 'InvalidQuery'}, status=400)
    r = get(client, 'Chicago, IL', 'Dallas, TX')
    assert r.status_code == 502 and r.json()['code'] == 'routing_error'


def test_post_not_allowed(client):
    assert client.post(URL).status_code == 405
