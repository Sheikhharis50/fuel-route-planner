import logging
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from ..errors import RouteInfeasible

logger = logging.getLogger('routing.optimizer')

_EPS_MI = 1e-6
_MIN_GALLONS = 1e-6


@dataclass(frozen=True)
class Stop:
    candidate: object  # stations.Candidate
    gallons: float
    cost: Decimal
    leg_miles: float  # route miles since the previous fueling point (start or previous stop)
    fuel_on_arrival_gal: float


@dataclass
class FuelPlan:
    stops: list = field(default_factory=list)
    total_cost: Decimal = Decimal('0')
    gallons_purchased: float = 0.0
    gallons_consumed: float = 0.0
    candidates_considered: int = 0


def to_cents(value):
    return Decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def plan_fuel_stops(candidates, total_miles, range_miles, mpg):
    """Greedy optimal refuelling with a full tank at start; candidates sorted by mile with .station.price."""
    inf = float('inf')
    # (mile, price, candidate); start can't buy (price inf), finish is a free sink (price 0).
    pts = [(0.0, inf, None)]
    pts += [(c.mile, c.station.price, c) for c in candidates if 0 <= c.mile <= total_miles]
    pts.append((total_miles, 0.0, None))

    for a, b in zip(pts, pts[1:]):
        if b[0] - a[0] > range_miles + _EPS_MI:
            logger.warning('infeasible gap from_mile=%.1f to_mile=%.1f gap=%.1f', a[0], b[0], b[0] - a[0])
            raise RouteInfeasible(
                f'No fuel station within range between mile {a[0]:.0f} and mile {b[0]:.0f} '
                f'({b[0] - a[0]:.0f} mi > {range_miles:.0f} mi range).',
                gap_start_mile=round(a[0], 1), gap_end_mile=round(b[0], 1))

    plan = FuelPlan(gallons_consumed=total_miles / mpg, candidates_considered=len(candidates))
    fuel = range_miles  # in miles of driving
    last_fuel_mile = 0.0
    i, last = 0, len(pts) - 1
    while i < last:
        mile_i, price_i, cand = pts[i]
        target, cheapest = None, None
        j = i + 1
        while j <= last and pts[j][0] - mile_i <= range_miles + _EPS_MI:
            if pts[j][1] < price_i:
                target = j
                break
            if cheapest is None or pts[j][1] <= pts[cheapest][1]:
                cheapest = j
            j += 1

        if target is not None:
            buy = max(0.0, (pts[target][0] - mile_i) - fuel)
        else:
            target = cheapest
            buy = range_miles - fuel

        gallons = buy / mpg
        if cand is not None and gallons > _MIN_GALLONS:
            cost = Decimal(repr(gallons)) * Decimal(repr(cand.station.price))
            stop = Stop(candidate=cand, gallons=gallons, cost=cost, leg_miles=mile_i - last_fuel_mile,
                        fuel_on_arrival_gal=fuel / mpg)
            plan.stops.append(stop)
            plan.total_cost += cost
            plan.gallons_purchased += gallons
            last_fuel_mile = mile_i
            logger.info('fuel stop mile=%.1f station=%s %r %s,%s price=%.4f gallons=%.2f cost=%.2f '
                        'arrival_fuel_gal=%.2f', mile_i, cand.station.id, cand.station.name, cand.station.city,
                        cand.station.state, cand.station.price, gallons, cost, fuel / mpg)
        fuel = max(0.0, fuel + buy - (pts[target][0] - mile_i))
        i = target

    logger.info('fuel plan stops=%d total_cost=%.2f gallons_purchased=%.2f gallons_consumed=%.2f '
                'candidates=%d final_leg_mi=%.1f', len(plan.stops), plan.total_cost, plan.gallons_purchased,
                plan.gallons_consumed, len(candidates), total_miles - last_fuel_mile)
    return plan
