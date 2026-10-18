from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.utils import timezone
from psycopg.types.range import DateRange

from apps.bookings.models import BOOKING_CODE_ALPHABET, BookingRoom
from apps.rooms.tests.factories import RoomFactory, RoomTypeFactory

from .factories import BookingFactory, BookingNightRateFactory, BookingRoomFactory, GuestFactory

pytestmark = pytest.mark.django_db


def day(offset):
    return timezone.localdate() + timedelta(days=offset)


def book(room, check_in, check_out, **kwargs):
    """Insert a BookingRoom straight into the DB (no full_clean), like a racing request would."""
    booking = BookingFactory(property=room.property, check_in_date=check_in, check_out_date=check_out)
    return BookingRoomFactory(booking=booking, room=room, **kwargs)


class TestOverlap:
    """The DB exclusion constraint is the last line of defence against double booking."""

    @pytest.mark.parametrize('check_in, check_out', [
        (10, 13),  # identical
        (11, 12),  # inside
        (9, 11),   # overlaps start
        (12, 15),  # overlaps end
        (8, 16),   # covers
    ])
    def test_overlapping_stay_on_same_room_rejected(self, hotel_a, check_in, check_out):
        room = RoomFactory(property=hotel_a)
        book(room, day(10), day(13))
        with pytest.raises(IntegrityError):
            book(room, day(check_in), day(check_out))

    def test_back_to_back_stays_allowed(self, hotel_a):
        room = RoomFactory(property=hotel_a)
        book(room, day(10), day(13))
        book(room, day(13), day(15))  # checks in the day the first guest checks out
        book(room, day(8), day(10))   # checks out the day the first guest checks in
        assert BookingRoom.objects.filter(room=room).count() == 3

    def test_same_dates_on_different_rooms_allowed(self, hotel_a):
        room_type = RoomTypeFactory(property=hotel_a)
        book(RoomFactory(property=hotel_a, room_type=room_type), day(10), day(13))
        book(RoomFactory(property=hotel_a, room_type=room_type), day(10), day(13))

    def test_cancelled_booking_releases_room(self, hotel_a):
        room = RoomFactory(property=hotel_a)
        book(room, day(10), day(13), is_active=False)
        book(room, day(10), day(13))

    def test_reactivating_cancelled_overlap_rejected(self, hotel_a):
        room = RoomFactory(property=hotel_a)
        cancelled = book(room, day(10), day(13), is_active=False)
        book(room, day(10), day(13))
        cancelled.is_active = True
        with pytest.raises(IntegrityError):
            cancelled.save()

    def test_full_clean_reports_overlap_in_thai(self, hotel_a):
        room = RoomFactory(property=hotel_a)
        book(room, day(10), day(13))
        booking = BookingFactory(property=hotel_a, check_in_date=day(11), check_out_date=day(12))
        clash = BookingRoom(
            property=hotel_a, booking=booking, room=room, room_type=room.room_type,
            stay_range=DateRange(day(11), day(12)),
        )
        with pytest.raises(ValidationError) as exc:
            clash.full_clean()
        assert 'ห้องนี้ถูกจองในช่วงวันที่เลือกแล้ว' in str(exc.value)

    @pytest.mark.parametrize('stay_range', [
        DateRange(None, None),
        DateRange(timezone.localdate(), None),
        DateRange(timezone.localdate(), timezone.localdate()),  # empty
    ])
    def test_unbounded_or_empty_stay_range_rejected(self, hotel_a, stay_range):
        room = RoomFactory(property=hotel_a)
        with pytest.raises(IntegrityError):
            book(room, day(1), day(2), stay_range=stay_range)


class TestBooking:
    def test_primary_key_is_uuid(self, hotel_a):
        assert BookingFactory(property=hotel_a).pk.version == 4

    def test_code_is_random_and_readable(self, hotel_a):
        codes = {BookingFactory(property=hotel_a).code for _ in range(5)}
        assert len(codes) == 5
        assert all(len(code) == 8 and set(code) <= set(BOOKING_CODE_ALPHABET) for code in codes)

    def test_same_code_allowed_in_different_properties(self, hotel_a, hotel_b):
        BookingFactory(property=hotel_a, code='ABCDEFGH')
        BookingFactory(property=hotel_b, code='ABCDEFGH')

    def test_duplicate_code_in_same_property_rejected(self, hotel_a):
        BookingFactory(property=hotel_a, code='ABCDEFGH')
        with pytest.raises(IntegrityError):
            BookingFactory(property=hotel_a, code='ABCDEFGH')

    def test_check_out_must_be_after_check_in(self, hotel_a):
        with pytest.raises(IntegrityError):
            BookingFactory(property=hotel_a, check_in_date=day(5), check_out_date=day(5))

    def test_nights(self, hotel_a):
        assert BookingFactory(property=hotel_a, check_in_date=day(1), check_out_date=day(4)).nights == 3


class TestGuest:
    def test_duplicate_email_in_same_property_rejected_case_insensitively(self, hotel_a):
        GuestFactory(property=hotel_a, email='a@example.com')
        with pytest.raises(IntegrityError):
            GuestFactory(property=hotel_a, email='A@Example.com')

    def test_same_email_allowed_in_different_properties(self, hotel_a, hotel_b):
        GuestFactory(property=hotel_a, email='a@example.com')
        GuestFactory(property=hotel_b, email='a@example.com')

    def test_full_clean_reports_duplicate_in_thai(self, hotel_a):
        GuestFactory(property=hotel_a, email='a@example.com')
        duplicate = GuestFactory.build(property=hotel_a, email='A@example.com')
        with pytest.raises(ValidationError) as exc:
            duplicate.full_clean()
        assert 'อีเมลนี้มีข้อมูลผู้เข้าพักในโรงแรมนี้แล้ว' in str(exc.value)


class TestCrossPropertyValidation:
    def test_booking_rejects_guest_from_other_property(self, hotel_a, hotel_b):
        booking = BookingFactory.build(property=hotel_a, guest=GuestFactory(property=hotel_b))
        with pytest.raises(ValidationError) as exc:
            booking.full_clean()
        assert 'guest' in exc.value.message_dict

    def test_booking_room_rejects_room_from_other_property(self, hotel_a, hotel_b):
        booking = BookingFactory(property=hotel_a)
        other_room = RoomFactory(property=hotel_b)
        booking_room = BookingRoom(
            property=hotel_a, booking=booking, room=other_room, room_type=other_room.room_type,
            stay_range=DateRange(booking.check_in_date, booking.check_out_date),
        )
        with pytest.raises(ValidationError) as exc:
            booking_room.full_clean()
        assert {'room', 'room_type'} <= set(exc.value.message_dict)

    def test_booking_room_rejects_room_of_different_type(self, hotel_a):
        booking = BookingFactory(property=hotel_a)
        room = RoomFactory(property=hotel_a)
        other_type = RoomTypeFactory(property=hotel_a)
        booking_room = BookingRoom(
            property=hotel_a, booking=booking, room=room, room_type=other_type,
            stay_range=DateRange(booking.check_in_date, booking.check_out_date),
        )
        with pytest.raises(ValidationError) as exc:
            booking_room.full_clean()
        assert 'room' in exc.value.message_dict


class TestBookingNightRate:
    def test_one_price_per_night(self, hotel_a):
        rate = BookingNightRateFactory(booking_room__booking__property=hotel_a)
        with pytest.raises(IntegrityError):
            BookingNightRateFactory(booking_room=rate.booking_room, night=rate.night)
