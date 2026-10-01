from decimal import Decimal
from types import SimpleNamespace

import numpy as np

from routing.services.geojson import build_feature_collection, simplify
from routing.services.optimizer import FuelPlan, Stop
from routing.services.stations import Candidate, Station


def test_simplify_straight_line_to_endpoints():
    pts = [(39.0, -100 + i * 0.01) for i in range(1000)]
    out = simplify(pts, 0.001)
    assert out.tolist() == [list(pts[0]), list(pts[-1])]


def test_simplify_keeps_corner():
    pts = [(0.0, i * 0.01) for i in range(100)] + [(i * 0.01, 1.0) for i in range(1, 100)]
    out = simplify(pts, 0.001).tolist()
    assert len(out) <= 4
    assert out[0] == [0.0, 0.0] and out[-1] == [0.99, 1.0] and [0.0, 0.99] in out


def test_simplify_preserves_shape_within_tolerance():
    t = np.linspace(0, 2 * np.pi, 5000)
    pts = np.column_stack((np.sin(t), t))
    out = simplify(pts, 0.001)
    assert len(out) < len(pts) / 5
    assert np.abs(np.interp(pts[:, 1], out[:, 1], out[:, 0]) - pts[:, 0]).max() < 0.0015


def _stop(mile, price, gallons, sid):
    st = Station(id=sid, name='N', address='A', city='C', state='TX', price=price, lat=35.0, lng=-97.0)
    cost = Decimal(repr(gallons)) * Decimal(repr(price))
    return Stop(candidate=Candidate(st, mile, 1.0), gallons=gallons, cost=cost, leg_miles=mile, fuel_on_arrival_gal=5)


def test_feature_collection_shape_and_totals():
    plan = FuelPlan(stops=[_stop(450, 3.333, 13.337, 'a'), _stop(900, 2.999, 27.111, 'b')],
                    gallons_purchased=40.448, gallons_consumed=120)
    plan.total_cost = sum((s.cost for s in plan.stops), Decimal(0))
    place = SimpleNamespace(name='X', state='TX', lat=30.0, lng=-95.0)
    route = SimpleNamespace(distance_m=1200 * 1609.344, duration_s=72000)
    meta = {'inputs': {'start': 'a', 'finish': 'b'}}
    fc = build_feature_collection(start=place, finish=place, route=route, route_points=[(30.0, -95.0), (31.0, -96.0)],
                                  plan=plan, vehicle={}, meta=meta)
    kinds = [f['properties']['kind'] for f in fc['features']]
    assert kinds == ['route', 'start', 'fuel_stop', 'fuel_stop', 'finish']
    assert fc['features'][0]['geometry']['coordinates'][0] == [-95.0, 30.0]
    assert fc['features'][2]['geometry']['coordinates'] == [-97.0, 35.0]
    costs = [f['properties']['cost'] for f in fc['features'] if f['properties']['kind'] == 'fuel_stop']
    assert fc['summary']['total_fuel_cost'] == round(sum(costs), 2)
    assert fc['summary']['number_of_stops'] == 2
