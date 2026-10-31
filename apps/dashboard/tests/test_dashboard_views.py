from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.bookings.models import Booking
from apps.bookings.tests.factories import (
    BookingFactory,
    BookingNightRateFactory,
    BookingRoomFactory,
)
from apps.properties.models import Membership
from apps.properties.tests.factories import MembershipFactory, PropertyFactory, UserFactory
from apps.rooms.tests.factories import RoomFactory

pytestmark = pytest.mark.django_db

Role = Membership.Role


def book(room, nights, status=Booking.Status.CONFIRMED):
    """One booking of `room`; `nights` maps each night to its snapshot price."""
    booking = BookingFactory(
        property=room.property,
        status=status,
        check_in_date=min(nights),
        check_out_date=max(nights) + timedelta(days=1),
    )
    booking_room = BookingRoomFactory(booking=booking, room=room)
    for night, price in nights.items():
        BookingNightRateFactory(booking_room=booking_room, night=night, price=Decimal(price))
    return booking


def hotel_url(hotel, **query):
    url = reverse('dashboard:hotel', kwargs={'property_slug': hotel.slug})
    return f'{url}?' + '&'.join(f'{key}={value}' for key, value in query.items()) if query else url


@pytest.fixture
def today():
    return timezone.localdate()


@pytest.fixture
def busy_hotel(hotel_a, today):
    """Two active rooms and 7% VAT; 2 nights sold, 1070 each (1000 without VAT)."""
    hotel_a.tax_rate = Decimal('7.00')
    hotel_a.save()
    room = RoomFactory(property=hotel_a)
    RoomFactory(property=hotel_a)
    book(room, {today - timedelta(days=2): '1070.00', today - timedelta(days=1): '1070.00'})
    return hotel_a


# ---------- Hotel dashboard (US-16) ----------

@pytest.mark.parametrize('role', [Role.OWNER, Role.MANAGER])
def test_owner_and_manager_see_the_kpis_of_the_last_30_days(client, busy_hotel, login_member, role, today):
    login_member(busy_hotel, role=role)

    response = client.get(hotel_url(busy_hotel))

    assert response.status_code == 200
    summary = response.context['summary']
    assert (summary.start, summary.end, summary.nights) == (today - timedelta(days=29), today, 30)
    assert summary.rooms_available == 60
    assert summary.rooms_sold == 2
    assert summary.room_revenue == Decimal('2000.00')
    assert summary.occupancy == Decimal('3.33')
    assert summary.adr == Decimal('1000.00')
    assert summary.revpar == Decimal('33.33')
    assert 'ไม่รวม VAT' in response.content.decode()


@pytest.mark.parametrize('role', [Role.FRONT_DESK, Role.HOUSEKEEPING])
def test_other_roles_of_the_same_hotel_are_refused(client, busy_hotel, login_member, role):
    login_member(busy_hotel, role=role)

    assert client.get(hotel_url(busy_hotel)).status_code == 403


def test_staff_of_another_hotel_get_404_for_every_method(client, busy_hotel, hotel_b, login_member,
                                                         assert_404_all_methods):
    login_member(hotel_b, role=Role.OWNER)

    assert_404_all_methods(hotel_url(busy_hotel))


def test_anonymous_visitors_are_sent_to_login(client, busy_hotel):
    response = client.get(hotel_url(busy_hotel))

    assert response.status_code == 302
    assert response['Location'].startswith('/accounts/login/')


def test_the_page_only_reads_data(client, busy_hotel, login_member):
    login_member(busy_hotel, role=Role.OWNER)

    assert client.post(hotel_url(busy_hotel)).status_code == 405


def test_another_hotels_numbers_never_reach_the_page(client, busy_hotel, hotel_b, login_member, today):
    book(RoomFactory(property=hotel_b), {today - timedelta(days=1): '123456.00'})
    login_member(busy_hotel, role=Role.OWNER)

    response = client.get(hotel_url(busy_hotel))

    content = response.content.decode()
    assert '123456' not in content and '123,456' not in content
    assert response.context['summary'].room_revenue == Decimal('2000.00')


def test_a_chosen_range_is_used(client, busy_hotel, login_member, today):
    login_member(busy_hotel, role=Role.OWNER)
    start, end = today - timedelta(days=2), today - timedelta(days=1)

    response = client.get(hotel_url(busy_hotel, start=start.isoformat(), end=end.isoformat()))

    summary = response.context['summary']
    assert (summary.nights, summary.rooms_available, summary.rooms_sold) == (2, 4, 2)
    assert summary.occupancy == Decimal('50.00')
    assert response.context['form']['start'].value() == start.isoformat()


def test_the_daily_chart_has_one_decimal_string_per_night(client, busy_hotel, login_member, today):
    login_member(busy_hotel, role=Role.OWNER)

    response = client.get(hotel_url(busy_hotel))

    chart = response.context['chart']
    assert len(chart) == 30
    assert [day['rooms_sold'] for day in chart[-3:]] == [1, 1, 0]
    content = response.content.decode()
    assert 'id="revenue-data"' in content
    assert '"revenue": "1000.00"' in content  # money stays a decimal string in the JSON
    assert 'id="revenue-chart"' in content


@pytest.mark.parametrize('query', [
    {'start': '2026-11-05', 'end': '2026-11-01'},
    {'start': '2025-01-01', 'end': '2026-11-01'},
    {'start': 'abc', 'end': 'def'},
    {'start': '2026-11-01'},
])
def test_a_bad_range_shows_the_errors_and_no_numbers(client, busy_hotel, login_member, query):
    login_member(busy_hotel, role=Role.OWNER)

    response = client.get(hotel_url(busy_hotel, **query))

    assert response.status_code == 200
    assert 'summary' not in response.context
    assert 'rr-field__errors' in response.content.decode()


def test_a_hotel_without_rooms_shows_dashes_instead_of_failing(client, hotel_b, login_member):
    login_member(hotel_b, role=Role.OWNER)

    response = client.get(hotel_url(hotel_b))

    assert response.status_code == 200
    assert response.context['summary'].occupancy is None
    assert '—' in response.content.decode()


def test_the_navbar_marks_the_dashboard_as_the_current_page(client, hotel_a, login_member):
    login_member(hotel_a, role=Role.MANAGER)

    content = client.get(hotel_url(hotel_a)).content.decode()

    assert f'href="{hotel_url(hotel_a)}" aria-current="page">แดชบอร์ด' in content


@pytest.mark.parametrize('role,sees_link', [(Role.OWNER, True), (Role.MANAGER, False)])
def test_only_the_owner_gets_the_link_to_the_comparison(client, hotel_a, login_member, role, sees_link):
    login_member(hotel_a, role=role)

    content = client.get(hotel_url(hotel_a)).content.decode()

    assert (f'href="{reverse("dashboard:compare")}"' in content) is sees_link


def test_query_count_does_not_grow_with_the_range(client, busy_hotel, login_member, today):
    login_member(busy_hotel, role=Role.OWNER)
    short = hotel_url(busy_hotel, start=(today - timedelta(days=2)).isoformat(), end=today.isoformat())
    long = hotel_url(busy_hotel, start=(today - timedelta(days=300)).isoformat(), end=today.isoformat())

    with CaptureQueriesContext(connection) as short_queries:
        client.get(short)
    with CaptureQueriesContext(connection) as long_queries:
        client.get(long)

    assert len(short_queries) == len(long_queries)


# ---------- Comparison of the owner's hotels (US-17) ----------

COMPARE = reverse('dashboard:compare')


def own(user, hotel, role=Role.OWNER):
    return MembershipFactory(user=user, property=hotel, role=role)


@pytest.fixture
def chain_owner(client):
    user = UserFactory()
    client.force_login(user)
    return user


@pytest.fixture
def two_hotels(chain_owner, today):
    """The owner's hotels, named so that the sort order differs from creation order."""
    riverside = PropertyFactory(name='Riverside', slug='riverside', tax_rate=Decimal('7.00'))
    alpine = PropertyFactory(name='Alpine', slug='alpine', tax_rate=Decimal('7.00'))
    for hotel, price in ((riverside, '1070.00'), (alpine, '2140.00')):
        own(chain_owner, hotel)
        book(RoomFactory(property=hotel), {today - timedelta(days=1): price})
    return riverside, alpine


def test_the_owner_sees_each_hotel_with_its_own_kpis(client, two_hotels):
    response = client.get(COMPARE)

    assert response.status_code == 200
    rows = response.context['rows']
    assert [row.property.name for row in rows] == ['Alpine', 'Riverside']
    assert [row.summary.room_revenue for row in rows] == [Decimal('2000.00'), Decimal('1000.00')]
    assert [row.summary.occupancy for row in rows] == [Decimal('3.33'), Decimal('3.33')]
    assert [row['name'] for row in response.context['chart']] == ['Alpine', 'Riverside']
    assert 'id="compare-data"' in response.content.decode()


def test_only_hotels_where_the_user_is_the_owner_appear(client, chain_owner, two_hotels):
    managed = PropertyFactory(name='Managed only', slug='managed-only')
    own(chain_owner, managed, role=Role.MANAGER)

    response = client.get(COMPARE)

    assert [row.property.name for row in response.context['rows']] == ['Alpine', 'Riverside']
    # The navbar's hotel switcher lists every membership, so look at the page body only
    body = response.content.decode().split('<main', 1)[1]
    assert 'Managed only' not in body


def test_hotels_of_other_owners_never_appear(client, two_hotels, today):
    stranger = PropertyFactory(name='Strangers Inn', slug='strangers-inn', tax_rate=Decimal('7.00'))
    own(UserFactory(), stranger)
    book(RoomFactory(property=stranger), {today - timedelta(days=1): '987654.00'})

    response = client.get(COMPARE)

    content = response.content.decode()
    assert 'Strangers Inn' not in content
    assert '987654' not in content and '987,654' not in content
    assert len(response.context['rows']) == 2


def test_closed_hotels_are_left_out(client, chain_owner, two_hotels):
    own(chain_owner, PropertyFactory(name='Closed hotel', slug='closed-hotel', is_active=False))

    response = client.get(COMPARE)

    assert 'Closed hotel' not in response.content.decode()


@pytest.mark.parametrize('role', [Role.MANAGER, Role.FRONT_DESK, Role.HOUSEKEEPING])
def test_users_who_own_no_hotel_are_refused(client, hotel_a, role):
    user = UserFactory()
    own(user, hotel_a, role=role)
    client.force_login(user)

    assert client.get(COMPARE).status_code == 403


def test_a_user_without_any_membership_is_refused(client):
    client.force_login(UserFactory())

    assert client.get(COMPARE).status_code == 403


def test_anonymous_visitors_cannot_compare(client):
    response = client.get(COMPARE)

    assert response.status_code == 302
    assert response['Location'].startswith('/accounts/login/')


def test_comparing_only_reads_data(client, two_hotels):
    assert client.post(COMPARE).status_code == 405


def test_a_bad_range_shows_the_errors_and_no_rows(client, two_hotels):
    response = client.get(COMPARE, {'start': '2026-11-05', 'end': '2026-11-01'})

    assert response.status_code == 200
    assert response.context['rows'] == []
    assert 'rr-field__errors' in response.content.decode()


def test_each_extra_hotel_costs_two_queries(client, chain_owner, two_hotels):
    with CaptureQueriesContext(connection) as before:
        client.get(COMPARE)
    third = PropertyFactory(name='Third', slug='third')
    own(chain_owner, third)

    with CaptureQueriesContext(connection) as after:
        client.get(COMPARE)

    assert len(after) - len(before) == 2
