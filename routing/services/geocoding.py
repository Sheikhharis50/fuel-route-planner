import csv
import logging
import re
import threading
import unicodedata
from dataclasses import dataclass

from django.conf import settings

from ..errors import InvalidLocationFormat, LocationError, UnsupportedLocation

logger = logging.getLogger('routing.geocoding')

US_STATES = frozenset(
    'AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ '
    'NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split()
)

_LSAD_SUFFIX = re.compile(
    r'\s+(city and borough|consolidated government|unified government|metropolitan government|'
    r'metro government|urban county|city|town|village|borough|municipality|cdp|ccd|plantation|'
    r'township|comunidad|zona urbana)(\s+\(balance\))?$',
    re.IGNORECASE,
)
_BALANCE = re.compile(r'\s+\(balance\)$')
_COUNTY_TAIL = re.compile(r'\s+county$')


@dataclass(frozen=True)
class Place:
    name: str
    state: str
    lat: float
    lng: float
    incorporated: bool


def normalize(name):
    s = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    s = s.replace('.', ' ').replace("'", '').replace(',', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    s = re.sub(r'^saint\b', 'st', s)
    s = re.sub(r'\bsaint\b', 'st', s)
    s = re.sub(r'^sainte\b', 'ste', s)
    s = re.sub(r'^fort\b', 'ft', s)
    s = re.sub(r'^mount\b', 'mt', s)
    s = re.sub(r'\bmc\s+', 'mc', s)
    s = re.sub(r'\bmac\s+', 'mac', s)
    return s


def _key(normalized):
    # Spacing/hyphen-insensitive: "De Forest" == "DeForest", "Dover Foxcroft" == "Dover-Foxcroft".
    return re.sub(r'[^a-z0-9]', '', normalized)


def _aliases(raw_name):
    """Yield (normalized_alias, tier); lower tier wins on collisions."""
    base = _BALANCE.sub('', raw_name.strip())
    stripped = _LSAD_SUFFIX.sub('', normalize(base))
    yield stripped, 0
    yield normalize(base), 1
    # Consolidated names like "Nashville-Davidson" or "Louisville/Jefferson County".
    for sep in ('-', '/'):
        if sep in stripped:
            yield _COUNTY_TAIL.sub('', stripped.split(sep)[0].strip()), 2
    # "Boise City city" -> also reachable as "Boise".
    if stripped.endswith(' city'):
        yield stripped[: -len(' city')], 3


class Gazetteer:
    """Census places (primary) plus optional county subdivisions (fallback, active governments only)."""

    def __init__(self, path, cousub_path=None):
        self._index = {}
        self._best = {}
        self._load(path, source_rank=0)
        if cousub_path is not None:
            self._load(cousub_path, source_rank=1, active_only=True)
        del self._best
        logger.info('gazetteer loaded keys=%d places=%s cousubs=%s', len(self._index), path, cousub_path)

    def _load(self, path, source_rank, active_only=False):
        best = self._best
        with open(path, encoding='utf-8', newline='') as fh:
            reader = csv.reader(fh, delimiter='\t')
            header = [h.strip() for h in next(reader)]
            col = {h: i for i, h in enumerate(header)}
            for row in reader:
                state = row[col['USPS']].strip()
                if state not in US_STATES:
                    continue
                funcstat = row[col['FUNCSTAT']].strip()
                if active_only and funcstat != 'A':
                    continue
                raw = row[col['NAME']].strip()
                place = Place(
                    name=_LSAD_SUFFIX.sub('', _BALANCE.sub('', raw)).strip() or raw,
                    state=state,
                    lat=float(row[col['INTPTLAT']]),
                    lng=float(row[col['INTPTLONG']].strip()),
                    incorporated=funcstat in ('A', 'F', 'B'),
                )
                aland = int(row[col['ALAND']])
                for alias, tier in _aliases(raw):
                    alias = _key(alias)
                    if not alias:
                        continue
                    key = (state, alias)
                    rank = (tier, source_rank, 0 if place.incorporated else 1, -aland, raw)
                    if key not in best or rank < best[key]:
                        best[key] = rank
                        self._index[key] = place

    def __len__(self):
        return len(self._index)

    def lookup(self, city, state):
        return self._index.get((state, _key(normalize(city))))


_gazetteer = None
_lock = threading.Lock()


def get_gazetteer():
    global _gazetteer
    if _gazetteer is None:
        with _lock:
            if _gazetteer is None:
                path = settings.GAZETTEER_FILE
                if not path.exists():
                    raise RuntimeError(f'Gazetteer file not found: {path}')
                cousub = settings.GAZETTEER_COUSUB_FILE
                _gazetteer = Gazetteer(path, cousub if cousub and cousub.exists() else None)
    return _gazetteer


def parse_location(text):
    if text is None or not str(text).strip():
        raise InvalidLocationFormat('Location is empty; expected "City, ST".', input=text)
    parts = str(text).rsplit(',', 1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise InvalidLocationFormat(f'Invalid location {text!r}; expected "City, ST" (e.g. "Chicago, IL").',
                                    input=text)
    city, state = parts[0].strip(), parts[1].strip().upper()
    if not re.fullmatch(r'[A-Z]{2}', state):
        raise InvalidLocationFormat(
            f'Invalid state in {text!r}; expected "City, ST" with a two-letter state code (e.g. "Chicago, IL").',
            input=text)
    if state not in US_STATES:
        raise UnsupportedLocation(f'{text!r} is not a supported US location (state {state!r}).', input=text)
    return city, state


def resolve(text, field=None):
    try:
        city, state = parse_location(text)
    except LocationError as exc:
        if field:
            exc.extra['field'] = field
        raise
    place = get_gazetteer().lookup(city, state)
    if place is None:
        extra = {'input': text}
        if field:
            extra['field'] = field
        raise LocationError(f'Could not find {city!r} in {state}.', **extra)
    logger.info('resolved field=%s input=%r -> %s, %s (%.5f, %.5f) incorporated=%s',
                field, text, place.name, place.state, place.lat, place.lng, place.incorporated)
    return place
