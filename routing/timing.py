import time
from contextlib import contextmanager


class StageTimer:
    def __init__(self):
        self._t0 = time.perf_counter()
        self.timings_ms = {}

    @contextmanager
    def stage(self, name):
        t = time.perf_counter()
        try:
            yield
        finally:
            self.timings_ms[name] = round((time.perf_counter() - t) * 1000, 2)

    def finish(self):
        self.timings_ms['total'] = round((time.perf_counter() - self._t0) * 1000, 2)
        return self.timings_ms
