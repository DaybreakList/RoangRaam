"""
Unpaid FULL bookings past PAYMENT_WINDOW must stop blocking rooms:
search ignores them without writing, and booking releases them for real.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.models import Booking, BookingRoom
from apps.bookings.payments import PAYMENT_WINDOW
from apps.bookings.services import (
    MAX_ROOMS_PER_BOOKING,
    TOO_MANY_ROOMS_MESSAGE,
    create_booking,
    get_available_room_types,
)
from apps.rooms.tests.factories import DailyRateFactory, RoomFactory, RoomTypeFactory

from .factories import BookingFactory, BookingRoomFactory

pytestmark = pytest.mark.django_db

GUEST = {'first_name': 'Somchai', 'last_name': 'Jaidee', 'email': 'somchai@example.com', 'phone': '0812345678'}


def day(offset):
    return timezone.localdate() + timedelta(days=offset)


def make_room_type(prop, rooms=1, name='Twin'):
    room_type = RoomTypeFactory(property=prop, name=name)
    for i in range(rooms):
        RoomFactory(property=prop, room_type=room_type, number=f'{name}-{i + 1:02d}')
    for offset in range(2):
        DailyRateFactory(property=prop, room_type=room_type, date=day(10 + offset), price=Decimal('1000.00'))
    return room_type


def hold(room, status=Booking.Status.PENDING_PAYMENT, age=PAYMENT_WINDOW + timedelta(minutes=1)):
    """A booking of `room` for day 10-12 created `age` ago."""
    booking = BookingFactory(
        property=room.property, status=status, payment_option=Booking.PaymentOption.FULL,
        check_in_date=day(10), check_out_date=day(12),
    )
    BookingRoomFactory(booking=booking, room=room)
    Booking.objects.filter(pk=booking.pk).update(created_at=timezone.now() - age)
    return booking


def available(prop):
    return {rt.name: rt.available_count for rt in get_available_room_types(prop, day(10), day(12))}


def book(prop, room_type, quantity=1):
    return create_booking(
        prop, check_in=day(10), check_out=day(12),
        rooms=[{'room_type_id': room_type.pk, 'quantity': quantity}],
        guest=GUEST, adults=1, children=0, payment_option='FULL',
    )


class TestSearchIgnoresExpiredHolds:
    def test_expired_unpaid_hold_does_not_block_search(self, hotel_a):
        twin = make_room_type(hotel_a)
        hold(twin.rooms.get())
        assert available(hotel_a) == {'Twin': 1}

    def test_search_does_not_write(self, hotel_a, django_assert_num_queries):
        twin = make_room_type(hotel_a)
        expired = hold(twin.rooms.get())
        with django_assert_num_queries(1):
            list(get_available_room_types(hotel_a, day(10), day(12)))
        expired.refresh_from_db()
        assert expired.status == Booking.Status.PENDING_PAYMENT
        assert BookingRoom.objects.get(booking=expired).is_active

    def test_unpaid_hold_inside_the_window_still_blocks(self, hotel_a):
        twin = make_room_type(hotel_a)
        hold(twin.rooms.get(), age=PAYMENT_WINDOW - timedelta(minutes=1))
        assert available(hotel_a) == {}

    @pytest.mark.parametrize('status', [Booking.Status.CONFIRMED, Booking.Status.CHECKED_IN])
    def test_old_paid_or_checked_in_booking_still_blocks(self, hotel_a, status):
        twin = make_room_type(hotel_a)
        hold(twin.rooms.get(), status=status, age=timedelta(days=3))
        assert available(hotel_a) == {}


class TestBookingReleasesExpiredHolds:
    def test_booking_takes_a_room_held_by_an_expired_unpaid_booking(self, hotel_a):
        twin = make_room_type(hotel_a)
        expired = hold(twin.rooms.get())

        booking = book(hotel_a, twin)

        expired.refresh_from_db()
        assert expired.status == Booking.Status.CANCELLED
        assert expired.cancelled_at is not None
        assert not BookingRoom.objects.get(booking=expired).is_active
        assert booking.booking_rooms.get().room == twin.rooms.get()

    def test_fresh_unpaid_hold_is_kept(self, hotel_a):
        twin = make_room_type(hotel_a)
        fresh = hold(twin.rooms.get(), age=PAYMENT_WINDOW - timedelta(minutes=1))

        with pytest.raises(ValidationError) as exc:
            book(hotel_a, twin)

        assert 'rooms' in exc.value.message_dict
        fresh.refresh_from_db()
        assert fresh.status == Booking.Status.PENDING_PAYMENT

    def test_only_the_requested_room_types_are_released(self, hotel_a):
        twin = make_room_type(hotel_a, name='Twin')
        suite = make_room_type(hotel_a, name='Suite')
        other = hold(suite.rooms.get())

        book(hotel_a, twin)

        other.refresh_from_db()
        assert other.status == Booking.Status.PENDING_PAYMENT

    def test_other_property_holds_are_not_touched(self, hotel_a, hotel_b):
        mine = make_room_type(hotel_a)
        theirs = make_room_type(hotel_b)
        hold(mine.rooms.get())
        their_hold = hold(theirs.rooms.get())

        book(hotel_a, mine)

        their_hold.refresh_from_db()
        assert their_hold.status == Booking.Status.PENDING_PAYMENT

    def test_a_failed_booking_rolls_the_release_back(self, hotel_a):
        """Asking for more rooms than exist fails; the expired hold is left for next time."""
        twin = make_room_type(hotel_a, rooms=1)
        expired = hold(twin.rooms.get())

        with pytest.raises(ValidationError):
            book(hotel_a, twin, quantity=2)

        expired.refresh_from_db()
        assert expired.status == Booking.Status.PENDING_PAYMENT


class TestMaxRoomsInService:
    def test_service_rejects_more_than_the_limit(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=MAX_ROOMS_PER_BOOKING + 1)
        with pytest.raises(ValidationError) as exc:
            book(hotel_a, twin, quantity=MAX_ROOMS_PER_BOOKING + 1)
        assert exc.value.message_dict == {'rooms': [TOO_MANY_ROOMS_MESSAGE]}
        assert not Booking.objects.exists()

    def test_limit_counts_repeated_entries_together(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=MAX_ROOMS_PER_BOOKING + 1)
        with pytest.raises(ValidationError):
            create_booking(
                hotel_a, check_in=day(10), check_out=day(12),
                rooms=[{'room_type_id': twin.pk, 'quantity': 15}, {'room_type_id': twin.pk, 'quantity': 6}],
                guest=GUEST, adults=1, children=0, payment_option='FULL',
            )

    def test_exactly_the_limit_is_allowed(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=MAX_ROOMS_PER_BOOKING)
        assert book(hotel_a, twin, quantity=MAX_ROOMS_PER_BOOKING).booking_rooms.count() == MAX_ROOMS_PER_BOOKING

    def test_message_is_thai_and_names_the_limit(self):
        assert TOO_MANY_ROOMS_MESSAGE == 'จองได้สูงสุด 20 ห้องต่อครั้ง'
