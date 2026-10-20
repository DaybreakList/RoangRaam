import re

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import URLResolver, get_resolver

from apps.properties.middleware import PROPERTY_URL_KWARG, _is_public
from apps.properties.models import Membership

pytestmark = pytest.mark.django_db

METHODS = ('get', 'post', 'patch', 'delete')
# Views open to guests. Adding one is a conscious decision: update this set and
# give the view its own isolation test.

PUBLIC_ROUTES = {
    'bookings:search', 'bookings:api-availability', 'bookings:api-booking-create',
    'bookings:pay', 'bookings:payment-result',
    'bookings:book', 'bookings:booking-confirmed',
    'bookings:manage-booking', 'bookings:manage-booking-cancel',
}

# Sample values so every route can be reversed; the middleware answers before
# the view runs, so the values only need to match the converter.
SAMPLE_VALUES = {'int': '1', 'slug': 'x', 'str': 'x', 'path': 'x',
                 'uuid': '00000000-0000-0000-0000-000000000000'}
CONVERTER = re.compile(r'<(?:(?P<type>\w+):)?(?P<name>\w+)>')

def _walk(patterns, prefix='', namespace=''):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            ns = f'{namespace}{pattern.namespace}:' if pattern.namespace else namespace
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern), ns)
        else:
            yield prefix + str(pattern.pattern), f'{namespace}{pattern.name}', pattern.callback


def property_routes():
    """(route, name, is_public) for every URL that takes <property_slug>."""
    return [
        (route, name, _is_public(callback))
        for route, name, callback in _walk(get_resolver().url_patterns)
        if f':{PROPERTY_URL_KWARG}>' in route
    ]


def build_url(route, slug):
    def fill(match):
        if match['name'] == PROPERTY_URL_KWARG:
            return slug
        return SAMPLE_VALUES[match['type'] or 'str']
    return '/' + CONVERTER.sub(fill, route)


PRIVATE = [(route, name) for route, name, public in property_routes() if not public]
NO_EXTRA_KWARGS = [(route, name) for route, name in PRIVATE if len(CONVERTER.findall(route)) == 1]


def _ids(cases):
    return [name for _, name in cases]


def test_sweep_finds_the_routes():
    names = {name for _, name in PRIVATE}
    assert {'property_home', 'rooms:rooms-list', 'rooms:price-calendar'} <= names


def test_public_routes_are_only_the_allowed_ones():
    public = {name for _, name, is_public in property_routes() if is_public}
    assert public == PUBLIC_ROUTES


@pytest.mark.parametrize('route,name', NO_EXTRA_KWARGS, ids=_ids(NO_EXTRA_KWARGS))
def test_own_member_can_open_page(client, hotel_a, login_member, route, name):
    # Happy path: proves the 404s below come from isolation, not from broken URLs.
    login_member(hotel_a, role=Membership.Role.OWNER)
    assert client.get(build_url(route, hotel_a.slug)).status_code == 200


@pytest.mark.parametrize('route,name', PRIVATE, ids=_ids(PRIVATE))
def test_other_hotel_staff_gets_404_and_writes_nothing(client, hotel_a, hotel_b, login_member, route, name):
    login_member(hotel_b, role=Membership.Role.OWNER)
    url = build_url(route, hotel_a.slug)
    with CaptureQueriesContext(connection) as queries:
        for method in METHODS:
            response = getattr(client, method)(url)
            assert response.status_code == 404, f'{method.upper()} {url} returned {response.status_code}'
    writes = [q['sql'] for q in queries if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
    assert writes == []


@pytest.mark.parametrize('route,name', PRIVATE, ids=_ids(PRIVATE))
def test_unknown_hotel_is_404(client, hotel_a, login_member, route, name):
    login_member(hotel_a, role=Membership.Role.OWNER)
    assert client.get(build_url(route, 'no-such-hotel')).status_code == 404