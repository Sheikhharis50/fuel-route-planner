import pytest

from routing.services.stations import (
    Station, StationIndex, find_candidates, get_station_index, haversine_mi, route_miles,
)


def make_station(id, lat, lng, price):
    return Station(id=id, name=f'S{id}', address='I-00', city='X', state='KS', price=price, lat=lat, lng=lng)


# A straight east-west route along latitude 39 with only two vertices (forces densification).
ROUTE = [(39.0, -100.0), (39.0, -95.0)]
ROUTE_MI = float(haversine_mi(39.0, -100.0, 39.0, -95.0))


def test_all_stations_load():
    assert len(get_station_index()) == 6626


def test_route_miles_densified_and_scaled():
    lat, lng, cum = route_miles(ROUTE, total_distance_mi=300.0)
    assert len(lat) > 500
    assert cum[0] == 0 and cum[-1] == pytest.approx(300.0)
    assert (cum[1:] >= cum[:-1]).all()


def test_in_corridor_station_with_mile_marker():
    on_route = make_station('a', 39.05, -97.5, 3.0)  # ~3.5 mi north of the line, halfway
    idx = StationIndex([on_route])
    cands = find_candidates(idx, ROUTE, ROUTE_MI, corridor_mi=10)
    assert len(cands) == 1
    assert cands[0].mile == pytest.approx(ROUTE_MI / 2, abs=1.0)
    assert 3 < cands[0].offset_mi < 4


def test_off_corridor_station_excluded():
    far = make_station('b', 39.5, -97.5, 2.0)  # ~35 mi away
    idx = StationIndex([far])
    assert find_candidates(idx, ROUTE, ROUTE_MI, corridor_mi=10) == []


def test_candidates_ordered_and_bounded():
    stations = [make_station(str(i), 39.01, -100 + i * 0.5, 3.0 + i / 100) for i in range(10)]
    cands = find_candidates(StationIndex(stations), ROUTE, ROUTE_MI, corridor_mi=10)
    miles = [c.mile for c in cands]
    assert miles == sorted(miles)
    assert all(0 <= m <= ROUTE_MI + 1e-6 for m in miles)


def test_same_position_collapses_to_cheapest():
    stations = [make_station('x', 39.02, -97.0, 3.50), make_station('y', 39.02, -97.0, 3.10),
                make_station('z', 39.02, -97.0, 3.30)]
    cands = find_candidates(StationIndex(stations), ROUTE, ROUTE_MI, corridor_mi=10)
    assert [c.station.id for c in cands] == ['y']
