import threading
from datetime import timedelta
from io import StringIO
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.db import connection, transaction
from django.utils import timezone

from apps.bookings.models import Booking
from apps.bookings.payments import PAYMENT_WINDOW, release_expired_bookings
from apps.properties.tests.factories import PropertyFactory

from .factories import BookingFactory, BookingRoomFactory

pytestmark = pytest.mark.django_db

PENDING = Booking.Status.PENDING_PAYMENT
WINDOW = int(PAYMENT_WINDOW / timedelta(minutes=1))


def _booking(prop, minutes_ago, status=PENDING):
    booking = BookingFactory(property=prop, status=status)
    BookingRoomFactory(booking=booking)
    created_at = timezone.now() - timedelta(minutes=minutes_ago)
    Booking.objects.filter(pk=booking.pk).update(created_at=created_at)
    return booking


def _status(booking):
    booking.refresh_from_db()
    return booking.status


@pytest.mark.parametrize('minutes_ago', [WINDOW, WINDOW + 1, 24 * 60])
def test_unpaid_booking_past_the_window_is_cancelled(hotel_a, minutes_ago):
    booking = _booking(hotel_a, minutes_ago)

    assert release_expired_bookings(hotel_a) == 1

    assert _status(booking) == Booking.Status.CANCELLED
    assert not booking.booking_rooms.filter(is_active=True).exists()


@pytest.mark.parametrize('minutes_ago,status', [
    (WINDOW - 1, PENDING),
    (WINDOW + 1, Booking.Status.CONFIRMED),
    (WINDOW + 1, Booking.Status.CHECKED_IN),
])
def test_booking_inside_the_window_or_not_waiting_for_payment_is_kept(hotel_a, minutes_ago, status):
    booking = _booking(hotel_a, minutes_ago, status)

    assert release_expired_bookings(hotel_a) == 0

    assert _status(booking) == status
    assert booking.booking_rooms.filter(is_active=True).exists()


def test_window_comes_from_payment_window(hotel_a):
    booking = _booking(hotel_a, minutes_ago=10)

    with mock.patch('apps.bookings.payments.PAYMENT_WINDOW', timedelta(minutes=5)):
        assert release_expired_bookings(hotel_a) == 1

    assert _status(booking) == Booking.Status.CANCELLED


def test_room_types_limit_what_is_released(hotel_a):
    wanted = _booking(hotel_a, minutes_ago=WINDOW + 1)
    other = _booking(hotel_a, minutes_ago=WINDOW + 1)
    wanted_type = wanted.booking_rooms.get().room_type

    assert release_expired_bookings(hotel_a, room_types=[wanted_type]) == 1
    assert _status(wanted) == Booking.Status.CANCELLED
    assert _status(other) == PENDING

    # Ids work as well as instances
    assert release_expired_bookings(hotel_a, room_types=[other.booking_rooms.get().room_type_id]) == 1
    assert _status(other) == Booking.Status.CANCELLED


def test_other_property_is_not_touched(hotel_a, hotel_b):
    foreign = _booking(hotel_b, minutes_ago=WINDOW + 1)
    foreign_type = foreign.booking_rooms.get().room_type

    assert release_expired_bookings(hotel_a) == 0
    assert release_expired_bookings(hotel_a, room_types=[foreign_type]) == 0

    assert _status(foreign) == PENDING


@pytest.mark.django_db(transaction=True)
def test_booking_locked_by_another_request_is_skipped():
    prop = PropertyFactory()
    booking = _booking(prop, minutes_ago=WINDOW + 1)
    locked, done = threading.Event(), threading.Event()

    def pay_in_another_request():
        try:
            with transaction.atomic():
                Booking.objects.select_for_update().get(pk=booking.pk)
                locked.set()
                done.wait(5)
        finally:
            connection.close()

    other = threading.Thread(target=pay_in_another_request)
    other.start()
    assert locked.wait(5)
    try:
        # Returns at once instead of waiting for the other request's lock
        assert release_expired_bookings(prop) == 0
    finally:
        done.set()
        other.join()

    assert _status(booking) == PENDING
    assert release_expired_bookings(prop) == 1


def test_command_releases_every_property(hotel_a, hotel_b):
    first, second = _booking(hotel_a, minutes_ago=WINDOW + 1), _booking(hotel_b, minutes_ago=WINDOW + 1)
    fresh = _booking(hotel_a, minutes_ago=5)
    out = StringIO()

    call_command('release_expired_bookings', stdout=out)

    assert _status(first) == _status(second) == Booking.Status.CANCELLED
    assert _status(fresh) == PENDING
    assert 'ยกเลิกการจองที่หมดเวลาชำระเงินแล้ว 2 การจอง' in out.getvalue()


def test_command_can_run_for_one_property(hotel_a, hotel_b):
    mine, other = _booking(hotel_a, minutes_ago=WINDOW + 1), _booking(hotel_b, minutes_ago=WINDOW + 1)

    call_command('release_expired_bookings', property=hotel_a.slug, stdout=StringIO())

    assert _status(mine) == Booking.Status.CANCELLED
    assert _status(other) == PENDING


def test_command_rejects_unknown_property(hotel_a):
    with pytest.raises(CommandError):
        call_command('release_expired_bookings', property='no-such-hotel', stdout=StringIO())
