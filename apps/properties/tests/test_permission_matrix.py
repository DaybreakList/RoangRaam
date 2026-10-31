"""
Role matrix of every staff page (TECH-09 / TASK-47).

PropertyMiddleware checks membership and test_isolation_sweep checks that other
hotels get 404. This file checks layer 2, the role: every staff route must be
listed in MATRIX with the roles allowed to open it, every other role gets 403
and nothing is written. A new staff route fails test_every_staff_route_is_listed
until its roles are added here and to docs/project/PERMISSIONS.md.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.tests.test_isolation_sweep import PRIVATE
from apps.bookings.tests.factories import BookingFactory, BookingRoomFactory
from apps.properties.models import Membership
from apps.rooms.models import Room
from apps.rooms.tests.factories import AmenityFactory, RoomFactory, RoomTypeFactory

pytestmark = pytest.mark.django_db

Role = Membership.Role
ALL = (Role.OWNER, Role.MANAGER, Role.FRONT_DESK, Role.HOUSEKEEPING)
FRONT_DESK = (Role.OWNER, Role.MANAGER, Role.FRONT_DESK)
MANAGEMENT = (Role.OWNER, Role.MANAGER)
CLEANING = (Role.OWNER, Role.MANAGER, Role.HOUSEKEEPING)

GET, POST = 'get', 'post'
READ_PAGE = ((GET,), FRONT_DESK)
WRITE_PAGE = ((GET, POST), MANAGEMENT)

# route name -> (methods the page answers, roles allowed)
MATRIX = {
    'property_home': ((GET,), ALL),
    # Inventory and prices (D)
    'rooms:price-calendar': READ_PAGE,
    'rooms:price-day': WRITE_PAGE,
    'rooms:price-bulk': WRITE_PAGE,
    **{
        f'rooms:{kind}-{action}': READ_PAGE if action in ('list', 'detail') else WRITE_PAGE
        for kind in ('room-types', 'rooms', 'amenities')
        for action in ('list', 'create', 'detail', 'edit', 'delete')
    },
    # Dashboard (E)
    'dashboard:hotel': ((GET,), MANAGEMENT),
    # Front desk (A, B, D, E)
    'tape_chart:index': ((GET,), FRONT_DESK),
    'daily_ops:index': ((GET,), FRONT_DESK),
    'check_in_out:check_in': ((POST,), FRONT_DESK),
    'check_in_out:check_out': ((POST,), FRONT_DESK),
    'room_status:index': ((GET,), ALL),
    'room_status:mark_clean': ((POST,), CLEANING),
}


@pytest.fixture
def url_for(hotel_a):
    """Real objects of hotel_a for the routes that take an id, so views get past their lookups."""
    room_type = RoomTypeFactory(property=hotel_a)
    room = RoomFactory(property=hotel_a, room_type=room_type, hk_status=Room.HousekeepingStatus.DIRTY)
    amenity = AmenityFactory(property=hotel_a)
    booking = BookingFactory(property=hotel_a)
    BookingRoomFactory(booking=booking, room=room)
    kwargs = {
        'rooms:price-day': {'pk': room_type.pk, 'day': (booking.check_in_date + timedelta(days=1)).isoformat()},
        'rooms:price-bulk': {'pk': room_type.pk},
        'check_in_out:check_in': {'booking_id': booking.pk},
        'check_in_out:check_out': {'booking_id': booking.pk},
        'room_status:mark_clean': {'room_id': room.pk},
    }
    for action in ('detail', 'edit', 'delete'):
        kwargs[f'rooms:room-types-{action}'] = {'pk': room_type.pk}
        kwargs[f'rooms:rooms-{action}'] = {'pk': room.pk}
        kwargs[f'rooms:amenities-{action}'] = {'pk': amenity.pk}

    def build(name):
        return reverse(name, kwargs={'property_slug': hotel_a.slug, **kwargs.get(name, {})})

    return build


def _cases(allowed):
    return [
        pytest.param(name, role, id=f'{name}-{role}')
        for name, (_, roles) in MATRIX.items()
        for role in ALL
        if (role in roles) is allowed
    ]


def test_every_staff_route_is_listed():
    assert {name for _, name in PRIVATE} == set(MATRIX)


@pytest.mark.parametrize('name, role', _cases(allowed=False))
def test_other_roles_get_403_and_write_nothing(client, hotel_a, login_member, url_for, name, role):
    url = url_for(name)
    login_member(hotel_a, role=role)
    methods, _ = MATRIX[name]

    with CaptureQueriesContext(connection) as queries:
        for method in methods:
            response = getattr(client, method)(url)
            assert response.status_code == 403, f'{role} {method.upper()} {url} returned {response.status_code}'

    writes = [q['sql'] for q in queries if q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))]
    assert writes == []


@pytest.mark.parametrize('name, role', _cases(allowed=True))
def test_allowed_roles_get_in(client, hotel_a, login_member, url_for, name, role):
    # Only the first method: a POST here may really change data, which is fine for an allowed role
    url = url_for(name)
    login_member(hotel_a, role=role)
    method = MATRIX[name][0][0]

    response = getattr(client, method)(url)

    assert response.status_code in (200, 302), f'{role} {method.upper()} {url} returned {response.status_code}'
