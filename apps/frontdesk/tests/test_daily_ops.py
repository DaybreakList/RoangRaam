from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.bookings.models import Booking
from apps.bookings.tests.factories import BookingFactory, BookingRoomFactory
from apps.frontdesk.services.daily_ops import daily_lists, property_today
from apps.properties.models import Membership
from apps.properties.tests.factories import PropertyFactory
from apps.rooms.tests.factories import RoomFactory

pytestmark = pytest.mark.django_db

Status = Booking.Status


def url(prop):
    return reverse('daily_ops:index', kwargs={'property_slug': prop.slug})


def book(prop, numbers, start, end, status=Status.CONFIRMED, active=True):
    """A booking of `prop` holding one room per number; days are offsets from the hotel's today."""
    today = property_today(prop)
    booking = BookingFactory(
        property=prop, status=status,
        check_in_date=today + timedelta(days=start), check_out_date=today + timedelta(days=end),
    )
    for number in numbers:
        BookingRoomFactory(booking=booking, room=RoomFactory(property=prop, number=number), is_active=active)
    return booking


def codes(rows):
    return [row.booking.code for row in rows]


@pytest.fixture
def today_bookings(hotel_a):
    shown = {
        'arriving': book(hotel_a, ['101'], 0, 2),
        'arrived': book(hotel_a, ['102'], 0, 1, Status.CHECKED_IN),
        'leaving': book(hotel_a, ['103'], -2, 0, Status.CHECKED_IN),
        'left': book(hotel_a, ['104'], -1, 0, Status.CHECKED_OUT),
        'staying': book(hotel_a, ['105', '106'], -1, 2, Status.CHECKED_IN),
    }
    # A room released from the staying booking must not be listed
    BookingRoomFactory(booking=shown['staying'], room=RoomFactory(property=hotel_a, number='107'), is_active=False)
    hidden = {
        'unpaid': book(hotel_a, ['108'], 0, 2, Status.PENDING_PAYMENT),
        'cancelled': book(hotel_a, ['109'], 0, 2, Status.CANCELLED, active=False),
        'tomorrow': book(hotel_a, ['110'], 1, 3),
        'left_yesterday': book(hotel_a, ['111'], -3, -1, Status.CHECKED_OUT),
    }
    return shown, hidden


def test_lists_hold_the_right_bookings(hotel_a, today_bookings):
    shown, _ = today_bookings

    lists = daily_lists(hotel_a)

    assert codes(lists['arrivals']) == [shown['arriving'].code, shown['arrived'].code]
    assert codes(lists['departures']) == [shown['leaving'].code, shown['left'].code]
    assert codes(lists['in_house']) == [shown['arrived'].code, shown['leaving'].code, shown['staying'].code]


def test_unpaid_cancelled_and_other_days_are_hidden(hotel_a, today_bookings):
    _, hidden = today_bookings

    lists = daily_lists(hotel_a)

    listed = {code for key in ('arrivals', 'departures', 'in_house') for code in codes(lists[key])}
    assert listed.isdisjoint(booking.code for booking in hidden.values())


def test_multi_room_booking_lists_its_active_rooms_only(hotel_a, today_bookings):
    shown, _ = today_bookings

    row = next(row for row in daily_lists(hotel_a)['in_house'] if row.booking == shown['staying'])

    assert row.room_numbers == ['105', '106']


def test_other_hotel_bookings_never_appear(hotel_a, hotel_b, today_bookings):
    foreign = book(hotel_b, ['101'], 0, 2)

    lists = daily_lists(hotel_a)

    listed = {code for key in ('arrivals', 'departures', 'in_house') for code in codes(lists[key])}
    assert foreign.code not in listed
    assert codes(daily_lists(hotel_b)['arrivals']) == [foreign.code]


def test_today_follows_the_hotel_timezone():
    # 20:00 UTC on 2 November is already 03:00 on 3 November in Bangkok
    moment = datetime(2026, 11, 2, 20, 0, tzinfo=dt_timezone.utc)
    bangkok = PropertyFactory(timezone='Asia/Bangkok')
    utc_hotel = PropertyFactory(timezone='UTC')

    with patch('django.utils.timezone.now', return_value=moment):
        assert property_today(bangkok).isoformat() == '2026-11-03'
        assert property_today(utc_hotel).isoformat() == '2026-11-02'
        assert daily_lists(bangkok)['today'].isoformat() == '2026-11-03'


def test_query_count_does_not_grow_with_bookings(hotel_a, today_bookings, django_assert_num_queries):
    for number in range(201, 211):
        book(hotel_a, [str(number)], 0, 2)

    with django_assert_num_queries(2):
        daily_lists(hotel_a)


@pytest.mark.parametrize('role', [Membership.Role.OWNER, Membership.Role.MANAGER, Membership.Role.FRONT_DESK])
def test_allowed_roles_see_the_page(client, hotel_a, login_member, today_bookings, role):
    shown, _ = today_bookings
    login_member(hotel_a, role)

    response = client.get(url(hotel_a))

    page = response.content.decode()
    assert response.status_code == 200
    assert shown['arriving'].code in page
    assert '105, 106' in page
    # Contact details stay out of the front desk list
    assert shown['arriving'].guest.email not in page
    assert shown['arriving'].guest.phone not in page


def test_housekeeping_is_denied(client, hotel_a, login_member):
    login_member(hotel_a, Membership.Role.HOUSEKEEPING)

    assert client.get(url(hotel_a)).status_code == 403


def test_anonymous_is_sent_to_login(client, hotel_a):
    response = client.get(url(hotel_a))

    assert response.status_code == 302
    assert '/accounts/login/' in response.url


@pytest.mark.parametrize('method', ['get', 'post', 'patch', 'delete'])
def test_other_hotel_staff_gets_404_and_writes_nothing(client, hotel_a, hotel_b, login_member, method):
    login_member(hotel_a, Membership.Role.OWNER)

    with CaptureQueriesContext(connection) as queries:
        response = getattr(client, method)(url(hotel_b))

    assert response.status_code == 404
    assert not any(
        query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for query in queries
    )


@pytest.mark.parametrize('method', ['post', 'patch', 'delete'])
def test_page_is_read_only(client, hotel_a, login_member, method):
    login_member(hotel_a)

    assert getattr(client, method)(url(hotel_a)).status_code == 405


def test_empty_day_shows_thai_messages(client, hotel_a, login_member):
    login_member(hotel_a)

    page = client.get(url(hotel_a)).content.decode()

    assert 'ไม่มีแขกที่จะเช็คอินวันนี้' in page
    assert 'ไม่มีแขกที่จะเช็คเอาต์วันนี้' in page
    assert 'ไม่มีแขกที่กำลังเข้าพัก' in page
