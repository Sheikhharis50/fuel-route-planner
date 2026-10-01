import random
from decimal import Decimal

import numpy as np
import pytest
from scipy.optimize import linprog

from routing.errors import RouteInfeasible
from routing.services.optimizer import plan_fuel_stops, to_cents
from routing.services.stations import Candidate, Station

R, MPG = 500.0, 10.0


def cand(mile, price, id=None):
    st = Station(id=id or f'{mile}', name='S', address='A', city='C', state='TX', price=price, lat=0, lng=0)
    return Candidate(station=st, mile=mile, offset_mi=0.0)


def simulate(plan, total, cands):
    """Replay the plan and return the max distance driven between fueling points and min fuel level."""
    fuel, prev, min_fuel, max_leg = R, 0.0, R, 0.0
    for s in plan.stops:
        fuel -= s.candidate.mile - prev
        min_fuel = min(min_fuel, fuel)
        fuel += s.gallons * MPG
        assert fuel <= R + 1e-6
        prev = s.candidate.mile
    fuel -= total - prev
    return min(min_fuel, fuel)


def lp_optimum(total, cands):
    n = len(cands)
    if n == 0:
        return 0.0
    miles = [c.mile for c in cands]
    prices = [c.station.price for c in cands]
    A, b = [], []
    for p in range(n + 1):
        mile_p = miles[p] if p < n else total
        # arrive with fuel >= 0: R + sum_{k<p} buy_k - mile_p >= 0
        A.append([-1.0 if k < p else 0.0 for k in range(n)])
        b.append(R - mile_p)
        if p < n:
            # tank not over-full after buying: R + sum_{k<=p} buy_k - mile_p <= R
            A.append([1.0 if k <= p else 0.0 for k in range(n)])
            b.append(mile_p)
    res = linprog(c=np.array(prices) / MPG, A_ub=A, b_ub=b, bounds=[(0, None)] * n, method='highs')
    assert res.status == 0
    return res.fun


def test_short_trip_no_stops():
    plan = plan_fuel_stops([cand(100, 3.0), cand(300, 2.5)], 480, R, MPG)
    assert plan.stops == [] and plan.total_cost == 0
    assert plan.gallons_consumed == pytest.approx(48)


def test_exactly_range_no_stops():
    assert plan_fuel_stops([], 500, R, MPG).stops == []


def test_prefers_cheaper_within_range_buys_just_enough():
    # Start full; at 450 ($4.00) the next cheaper station (700, $2.00) is in range -> buy only to reach it.
    plan = plan_fuel_stops([cand(450, 4.0), cand(700, 2.0)], 1100, R, MPG)
    s450, s700 = plan.stops
    # Arrive at 450 with 50 mi left; need 250 mi to reach 700 -> buy 200 mi = 20 gal.
    assert s450.gallons == pytest.approx(20)
    # At 700 (cheapest, finish 400 mi away within range) buy exactly enough to finish.
    assert s700.gallons == pytest.approx(40)


def test_fills_up_when_nothing_cheaper_in_range():
    plan = plan_fuel_stops([cand(400, 2.0), cand(800, 5.0)], 1200, R, MPG)
    assert plan.stops[0].candidate.mile == 400
    assert plan.stops[0].gallons == pytest.approx(40)  # arrive with 100 mi, fill to 500
    assert plan.stops[1].candidate.mile == 800
    assert plan.stops[1].gallons == pytest.approx(30)  # arrive with 100 mi, need 400 to finish


def test_cost_example_from_spec():
    # 900 mi trip, one stop at mile 500 buying 40 gallons at $3.00.
    plan = plan_fuel_stops([cand(500, 3.0)], 900, R, MPG)
    assert len(plan.stops) == 1
    assert plan.stops[0].gallons == pytest.approx(40)
    assert to_cents(plan.total_cost) == Decimal('120.00')


def test_zero_gallon_stops_dropped():
    plan = plan_fuel_stops([cand(100, 5.0), cand(450, 3.0)], 900, R, MPG)
    assert [s.candidate.mile for s in plan.stops] == [450]


@pytest.mark.parametrize('cands,total,gap', [
    ([cand(300, 3.0), cand(820, 3.0)], 1000, (300.0, 820.0)),
    ([cand(520, 3.0)], 800, (0.0, 520.0)),
    ([cand(400, 3.0)], 1000, (400.0, 1000.0)),
])
def test_infeasible_gap(cands, total, gap):
    with pytest.raises(RouteInfeasible) as ei:
        plan_fuel_stops(cands, total, R, MPG)
    assert (ei.value.extra['gap_start_mile'], ei.value.extra['gap_end_mile']) == gap


@pytest.mark.parametrize('seed', range(200))
def test_matches_lp_optimum_and_never_exceeds_range(seed):
    rng = random.Random(seed)
    total = rng.uniform(501, 2500)
    cands, mile = [], 0.0
    while True:
        mile += rng.uniform(5, 300)
        if mile >= total:
            break
        cands.append(cand(round(mile, 3), round(rng.uniform(2.7, 6.4), 4), id=f'{seed}-{len(cands)}'))
    try:
        plan = plan_fuel_stops(cands, total, R, MPG)
    except RouteInfeasible:
        return
    assert simulate(plan, total, cands) >= -1e-6
    assert all(s.leg_miles <= R + 1e-6 for s in plan.stops)
    assert float(plan.total_cost) == pytest.approx(lp_optimum(total, cands), rel=1e-7, abs=1e-6)
