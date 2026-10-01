import contextvars
import logging
import re
import uuid

request_id_var = contextvars.ContextVar('request_id', default='-')

_VALID_ID = re.compile(r'^[A-Za-z0-9._-]{1,64}$')


def get_request_id():
    return request_id_var.get()


_base_factory = logging.getLogRecordFactory()


def _record_factory(*args, **kwargs):
    record = _base_factory(*args, **kwargs)
    record.request_id = request_id_var.get()
    return record


logging.setLogRecordFactory(_record_factory)


class RequestIdLogFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, 'request_id'):
            record.request_id = request_id_var.get()
        return True


class RequestIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get('X-Request-ID', '')
        # Only echo client-supplied ids that are safe to put in logs and headers.
        rid = incoming if _VALID_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        try:
            request.request_id = rid
            response = self.get_response(request)
            response['X-Request-ID'] = rid
            return response
        finally:
            request_id_var.reset(token)
