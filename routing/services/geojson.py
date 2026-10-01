import numpy as np

from .optimizer import to_cents


def simplify(points, tolerance):
    """Ramer-Douglas-Peucker on (lat, lng) pairs; tolerance in degrees. Returns kept points."""
    pts = np.asarray(points, dtype=float)
    n = len(pts)
    if n <= 2 or tolerance <= 0:
        return pts
    keep = np.zeros(n, dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = pts[a + 1:b]
        p, q = pts[a], pts[b]
        d = q - p
        norm = np.hypot(*d)
        if norm == 0:
            dist = np.hypot(*(seg - p).T)
        else:
            dist = np.abs(d[0] * (seg[:, 1] - p[1]) - d[1] * (seg[:, 0] - p[0])) / norm
        k = int(np.argmax(dist))
        if dist[k] > tolerance:
            m = a + 1 + k
            keep[m] = True
            stack.append((a, m))
            stack.append((m, b))
    return pts[keep]


def _money(value):
    return float(to_cents(value))


def _point(lat, lng):
    return {'type': 'Point', 'coordinates': [round(lng, 6), round(lat, 6)]}


def build_feature_collection(*, start, finish, route, route_points, plan, vehicle, meta):
    line = [[round(lng, 6), round(lat, 6)] for lat, lng in route_points]
    distance_mi = route.distance_m / 1609.344

    stop_features = []
    for n, s in enumerate(plan.stops, start=1):
        st = s.candidate.station
        stop_features.append({
            'type': 'Feature',
            'geometry': _point(st.lat, st.lng),
            'properties': {
                'kind': 'fuel_stop',
                'stop_number': n,
                'station_id': st.id,
                'name': st.name,
                'address': st.address,
                'city': st.city,
                'state': st.state,
                'price_per_gallon': round(st.price, 4),
                'mile_marker': round(s.candidate.mile, 1),
                'leg_miles': round(s.leg_miles, 1),
                'fuel_on_arrival_gallons': round(s.fuel_on_arrival_gal, 2),
                'gallons': round(s.gallons, 3),
                'cost': _money(s.cost),
                'distance_from_route_miles': round(s.candidate.offset_mi, 2),
                'location_precision': 'city',
            },
        })

    # Sum rounded stop costs so the total always equals the sum shown per stop.
    total_cost = sum((to_cents(s.cost) for s in plan.stops), start=to_cents(0))
    gallons_purchased = plan.gallons_purchased

    features = [
        {
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': line},
            'properties': {
                'kind': 'route',
                'distance_miles': round(distance_mi, 1),
                'duration_seconds': round(route.duration_s),
            },
        },
        {
            'type': 'Feature',
            'geometry': _point(start.lat, start.lng),
            'properties': {'kind': 'start', 'city': start.name, 'state': start.state, 'input': meta['inputs']['start']},
        },
        *stop_features,
        {
            'type': 'Feature',
            'geometry': _point(finish.lat, finish.lng),
            'properties': {'kind': 'finish', 'city': finish.name, 'state': finish.state,
                           'input': meta['inputs']['finish'], 'mile_marker': round(distance_mi, 1)},
        },
    ]

    summary = {
        'currency': 'USD',
        'total_distance_miles': round(distance_mi, 1),
        'total_duration_seconds': round(route.duration_s),
        'total_duration_hours': round(route.duration_s / 3600, 2),
        'total_fuel_cost': float(total_cost),
        'total_gallons_purchased': round(gallons_purchased, 3),
        'total_gallons_consumed': round(plan.gallons_consumed, 3),
        'number_of_stops': len(plan.stops),
        'average_price_paid_per_gallon': round(float(total_cost) / gallons_purchased, 4) if gallons_purchased else None,
        'vehicle': vehicle,
        'notes': 'Starting tank is full and not included in total_fuel_cost.',
    }
    return {'type': 'FeatureCollection', 'features': features, 'summary': summary, 'meta': meta}
