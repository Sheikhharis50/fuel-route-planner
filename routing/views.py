import logging

from django.conf import settings
from rest_framework import serializers
from rest_framework.response import Response
from rest_framework.views import APIView

from .middleware import get_request_id
from .services import geocoding, osrm, stations
from .services.geojson import build_feature_collection, simplify
from .services.optimizer import plan_fuel_stops
from .timing import StageTimer

logger = logging.getLogger('routing.api')

METERS_PER_MILE = 1609.344


class RouteQuerySerializer(serializers.Serializer):
    start = serializers.CharField(max_length=120, trim_whitespace=True)
    finish = serializers.CharField(max_length=120, trim_whitespace=True)


class RouteView(APIView):
    def get(self, request):
        timer = StageTimer()
        query = RouteQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        start_in, finish_in = query.validated_data['start'], query.validated_data['finish']
        logger.info('route request start=%r finish=%r', start_in, finish_in)

        with timer.stage('resolve'):
            start = geocoding.resolve(start_in, field='start')
            finish = geocoding.resolve(finish_in, field='finish')

        with timer.stage('route'):
            result = osrm.get_route((start.lat, start.lng), (finish.lat, finish.lng))
        route = result.route
        distance_mi = route.distance_m / METERS_PER_MILE

        corridor = settings.FUEL_CORRIDOR_MILES
        with timer.stage('corridor'):
            candidates = stations.find_candidates(stations.get_station_index(), route.points, distance_mi, corridor)

        range_mi, mpg = settings.VEHICLE_RANGE_MILES, settings.VEHICLE_MPG
        with timer.stage('optimize'):
            plan = plan_fuel_stops(candidates, distance_mi, range_mi, mpg)

        with timer.stage('simplify'):
            line = simplify(route.points, settings.ROUTE_SIMPLIFY_TOLERANCE_DEG)

        meta = {
            'request_id': get_request_id(),
            'inputs': {'start': start_in, 'finish': finish_in},
            'osrm_calls': result.osrm_calls,
            'cache_hit': result.cache_hit,
            'osrm_latency_ms': result.osrm_latency_ms,
            'candidate_stations': len(candidates),
            'corridor_miles': corridor,
            'route_points_full': len(route.points),
            'route_points_returned': len(line),
            'resolved': {
                'start': {'name': start.name, 'state': start.state, 'lat': start.lat, 'lng': start.lng},
                'finish': {'name': finish.name, 'state': finish.state, 'lat': finish.lat, 'lng': finish.lng},
            },
            'data_sources': {
                'routing': settings.OSRM_BASE_URL,
                'stations': len(stations.get_station_index()),
                'geocoding': 'US Census Gazetteer 2024 (local)',
                'attribution': 'Routing (c) OpenStreetMap contributors, OSRM',
            },
            'timings_ms': timer.timings_ms,
        }
        vehicle = {'range_miles': range_mi, 'mpg': mpg, 'tank_gallons': range_mi / mpg,
                   'starting_fuel_gallons': range_mi / mpg}

        with timer.stage('serialize'):
            body = build_feature_collection(start=start, finish=finish, route=route, route_points=line,
                                            plan=plan, vehicle=vehicle, meta=meta)
        timer.finish()
        s = body['summary']
        logger.info('route response distance_mi=%.1f stops=%d total_cost=%.2f gallons_purchased=%.2f '
                    'osrm_calls=%d cache_hit=%s candidates=%d points=%d->%d timings_ms=%s',
                    s['total_distance_miles'], s['number_of_stops'], s['total_fuel_cost'],
                    s['total_gallons_purchased'], result.osrm_calls, result.cache_hit, len(candidates),
                    len(route.points), len(line), timer.timings_ms)
        return Response(body)
