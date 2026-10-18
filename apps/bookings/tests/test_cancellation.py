from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.cancellation import cancel_booking
from apps.bookings.models import Booking, BookingRoom
from apps.bookings.services import get_available_room_types
from apps.rooms.tests.factories import DailyRateFactory, RoomFactory

from .factories import BookingFactory, BookingRoomFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def booking(hotel_a):
    """A confirmed booking of hotel A holding two rooms."""
    booking = BookingFactory(property=hotel_a)
    BookingRoomFactory(booking=booking)
    BookingRoomFactory(booking=booking)
    return booking


def test_cancel_marks_booking_and_releases_rooms(hotel_a, booking):
    cancel_booking(hotel_a, booking.pk)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    assert booking.cancelled_at is not None
    assert not booking.booking_rooms.filter(is_active=True).exists()


def test_pending_payment_booking_can_be_cancelled(hotel_a):
    booking = BookingFactory(property=hotel_a, status=Booking.Status.PENDING_PAYMENT)
    cancel_booking(hotel_a, booking.pk)
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED


def test_released_room_shows_in_search_and_can_be_booked_again(hotel_a):
    room = RoomFactory(property=hotel_a)
    check_in = timezone.localdate() + timedelta(days=7)
    check_out = check_in + timedelta(days=2)
    for offset in range(2):
        DailyRateFactory(property=hotel_a, room_type=room.room_type, date=check_in + timedelta(days=offset))
    booking = BookingFactory(property=hotel_a, check_in_date=check_in, check_out_date=check_out)
    BookingRoomFactory(booking=booking, room=room)
    assert room.room_type not in get_available_room_types(hotel_a, check_in, check_out)

    cancel_booking(hotel_a, booking.pk)

    assert room.room_type in get_available_room_types(hotel_a, check_in, check_out)
    # The exclusion constraint must no longer block the same room and dates.
    again = BookingFactory(property=hotel_a, check_in_date=check_in, check_out_date=check_out)
    BookingRoomFactory(booking=again, room=room)


@pytest.mark.parametrize('status', [
    Booking.Status.CHECKED_IN,
    Booking.Status.CHECKED_OUT,
    Booking.Status.CANCELLED,
])
def test_booking_past_confirmation_cannot_be_cancelled(hotel_a, status):
    booking = BookingFactory(property=hotel_a, status=status)
    booking_room = BookingRoomFactory(booking=booking)

    with pytest.raises(ValidationError):
        cancel_booking(hotel_a, booking.pk)

    booking.refresh_from_db()
    booking_room.refresh_from_db()
    assert booking.status == status
    assert booking_room.is_active


def test_other_bookings_keep_their_rooms(hotel_a, booking):
    other = BookingFactory(property=hotel_a)
    other_room = BookingRoomFactory(booking=other)

    cancel_booking(hotel_a, booking.pk)

    other.refresh_from_db()
    other_room.refresh_from_db()
    assert other.status == Booking.Status.CONFIRMED
    assert other_room.is_active


def test_other_hotel_cannot_cancel(hotel_b, booking):
    with pytest.raises(Booking.DoesNotExist):
        cancel_booking(hotel_b, booking.pk)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.cancelled_at is None
    assert BookingRoom.objects.filter(booking=booking, is_active=True).count() == 2