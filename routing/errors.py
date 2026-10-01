import logging

from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger('routing')


class RoutePlannerError(Exception):
    status_code = 500
    code = 'internal_error'

    def __init__(self, detail, **extra):
        super().__init__(detail)
        self.detail = detail
        self.extra = extra


class LocationError(RoutePlannerError):
    status_code = 400
    code = 'location_not_found'


class InvalidLocationFormat(LocationError):
    code = 'invalid_location_format'


class UnsupportedLocation(LocationError):
    code = 'unsupported_location'


class NoRouteError(RoutePlannerError):
    status_code = 422
    code = 'no_route'


class RouteInfeasible(RoutePlannerError):
    status_code = 422
    code = 'route_infeasible'


class RoutingError(RoutePlannerError):
    status_code = 502
    code = 'routing_error'


class RoutingUnavailable(RoutePlannerError):
    status_code = 503
    code = 'routing_unavailable'


def _error_body(code, detail, extra=None):
    from .middleware import get_request_id

    body = {'code': code, 'detail': detail, 'request_id': get_request_id()}
    if extra:
        body.update(extra)
    return body


def api_exception_handler(exc, context):
    if isinstance(exc, RoutePlannerError):
        logger.warning('request failed category=%s status=%s detail=%r extra=%s',
                       exc.code, exc.status_code, exc.detail, exc.extra)
        return Response(_error_body(exc.code, exc.detail, exc.extra), status=exc.status_code)

    response = exception_handler(exc, context)
    if response is None:
        logger.exception('unhandled error')
        return Response(_error_body('internal_error', 'Internal server error.'), status=500)

    if isinstance(exc, ValidationError):
        code, detail = 'invalid_request', response.data
    elif isinstance(exc, APIException):
        code, detail = exc.default_code, response.data.get('detail', response.data)
    else:
        code, detail = 'error', response.data
    logger.warning('request failed category=%s status=%s detail=%r', code, response.status_code, detail)
    response.data = _error_body(code, detail)
    return response
