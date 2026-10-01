import json
import logging

import pytest
from django.conf import settings

from routing.errors import InvalidLocationFormat, LocationError, UnsupportedLocation
from routing.services.geocoding import Gazetteer, get_gazetteer, normalize, resolve


def test_known_city_resolves_without_network(monkeypatch):
    import socket

    def no_network(*a, **kw):
        raise AssertionError('network access attempted')

    monkeypatch.setattr(socket.socket, 'connect', no_network)
    place = resolve('Chicago, IL')
    assert place.state == 'IL'
    assert 41.6 < place.lat < 42.1 and -87.95 < place.lng < -87.5


def test_case_and_whitespace_insensitive():
    assert resolve('  chicago ,  il ') == resolve('Chicago, IL')


@pytest.mark.parametrize('a,b', [
    ('St. Louis, MO', 'Saint Louis, MO'),
    ('St Louis, MO', 'Saint Louis, MO'),
    ('Mc Calla, AL', 'McCalla, AL'),
    ('Ft. Worth, TX', 'Fort Worth, TX'),
])
def test_name_variants_equivalent(a, b):
    assert resolve(a) == resolve(b)


@pytest.mark.parametrize('text,expected_name', [
    ('Pell City, AL', 'Pell City'),
    ('Phenix City, AL', 'Phenix City'),
    ('Nashville, TN', 'Nashville-Davidson'),
    ('Indianapolis, IN', 'Indianapolis'),
    ('Boise, ID', 'Boise City'),
    ('De Forest, WI', 'DeForest'),
    ('Dover Foxcroft, ME', 'Dover-Foxcroft'),
])
def test_tricky_census_names(text, expected_name):
    assert resolve(text).name == expected_name


def test_louisville_resolves_to_metro():
    place = resolve('Louisville, KY')
    assert 38.0 < place.lat < 38.4 and -85.9 < place.lng < -85.5


def test_new_england_town_from_cousubs():
    place = resolve('Auburn, NH')
    assert place.state == 'NH' and 42.9 < place.lat < 43.1


def test_incorporated_preferred_over_cdp(tmp_path):
    header = 'USPS\tGEOID\tANSICODE\tNAME\tLSAD\tFUNCSTAT\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG\n'
    rows = [
        'TX\t1\t1\tSpringfield CDP\t57\tS\t9000000\t0\t1\t0\t30.0\t-97.0\n',
        'TX\t2\t2\tSpringfield city\t25\tA\t100\t0\t1\t0\t31.0\t-98.0\n',
    ]
    path = tmp_path / 'gaz.txt'
    path.write_text(header + ''.join(rows))
    for order in (rows, rows[::-1]):
        path.write_text(header + ''.join(order))
        place = Gazetteer(path).lookup('Springfield', 'TX')
        assert place.incorporated and place.lat == 31.0


def test_deterministic():
    assert all(resolve('Springfield, IL') == resolve('Springfield, IL') for _ in range(3))


def test_unknown_city():
    with pytest.raises(LocationError) as ei:
        resolve('Nowheresville, KS', field='start')
    assert ei.value.code == 'location_not_found'
    assert ei.value.extra == {'input': 'Nowheresville, KS', 'field': 'start'}


def test_non_us_state():
    with pytest.raises(UnsupportedLocation):
        resolve('Toronto, ON')


@pytest.mark.parametrize('text', ['Chicago', '', '   ', ', IL', 'Chicago, Illinois', None])
def test_malformed(text):
    with pytest.raises(InvalidLocationFormat) as ei:
        resolve(text)
    assert 'City, ST' in ei.value.detail


def test_normalize():
    assert normalize("  St.  Mary's ") == 'st marys'
    assert normalize('Española') == 'espanola'


def test_station_city_coverage():
    stations = json.loads(settings.STATIONS_FILE.read_text())
    pairs = {(s['city'].strip(), s['state']) for s in stations}
    gaz = get_gazetteer()
    misses = sorted(p for p in pairs if gaz.lookup(*p) is None)
    coverage = 1 - len(misses) / len(pairs)
    logging.getLogger('routing.tests').warning('station city coverage=%.3f misses=%d sample=%s',
                                               coverage, len(misses), misses[:15])
    assert coverage >= 0.95, (coverage, misses[:30])
