from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from apps.bookings.services import get_available_room_types
from apps.rooms.tests.factories import DailyRateFactory, RoomFactory, RoomTypeFactory

from .factories import BookingFactory, BookingRoomFactory

pytestmark = pytest.mark.django_db


def day(offset):
    return timezone.localdate() + timedelta(days=offset)


def make_room_type(prop, rooms=1, prices=(Decimal('1000.00'),) * 3, start=10, **kwargs):
    """Room type with `rooms` rooms and a DailyRate for each night from day(start)."""
    room_type = RoomTypeFactory(property=prop, **kwargs)
    for _ in range(rooms):
        RoomFactory(property=prop, room_type=room_type)
    for offset, price in enumerate(prices):
        DailyRateFactory(property=prop, room_type=room_type, date=day(start + offset), price=price)
    return room_type


def book(room, check_in, check_out, is_active=True):
    booking = BookingFactory(property=room.property, check_in_date=check_in, check_out_date=check_out)
    return BookingRoomFactory(booking=booking, room=room, is_active=is_active)


def as_dict(results):
    return {rt.name: (rt.available_count, rt.total_price) for rt in results}


class TestAvailability:
    def test_counts_free_rooms_and_sums_per_night_prices(self, hotel_a):
        room_type = make_room_type(
            hotel_a, rooms=3, name='Deluxe',
            prices=[Decimal('1000.00'), Decimal('1500.00'), Decimal('1200.50')],
        )
        book(room_type.rooms.first(), day(10), day(12))

        result = as_dict(get_available_room_types(hotel_a, day(10), day(13)))

        # Per-night sum, not 1000 x 3
        assert result == {'Deluxe': (2, Decimal('3700.50'))}
        assert isinstance(result['Deluxe'][1], Decimal)

    def test_price_only_counts_requested_nights(self, hotel_a):
        make_room_type(
            hotel_a, name='Deluxe',
            prices=[Decimal('1000.00'), Decimal('2000.00'), Decimal('4000.00')],
        )
        result = as_dict(get_available_room_types(hotel_a, day(11), day(12)))
        assert result == {'Deluxe': (1, Decimal('2000.00'))}

    def test_back_to_back_booking_does_not_block(self, hotel_a):
        room_type = make_room_type(hotel_a, name='Deluxe')
        room = room_type.rooms.get()
        book(room, day(7), day(10))   # checks out the day we check in
        book(room, day(13), day(15))  # checks in the day we check out
        assert as_dict(get_available_room_types(hotel_a, day(10), day(13)))['Deluxe'][0] == 1

    def test_cancelled_booking_does_not_block(self, hotel_a):
        room_type = make_room_type(hotel_a, name='Deluxe')
        book(room_type.rooms.get(), day(10), day(13), is_active=False)
        assert as_dict(get_available_room_types(hotel_a, day(10), day(13)))['Deluxe'][0] == 1

    def test_inactive_rooms_not_counted(self, hotel_a):
        room_type = make_room_type(hotel_a, rooms=2, name='Deluxe')
        room = room_type.rooms.first()
        room.is_active = False
        room.save()
        assert as_dict(get_available_room_types(hotel_a, day(10), day(13)))['Deluxe'][0] == 1

    def test_fully_booked_type_excluded(self, hotel_a):
        full = make_room_type(hotel_a, name='Full')
        make_room_type(hotel_a, name='Free')
        book(full.rooms.get(), day(11), day(12))
        assert set(as_dict(get_available_room_types(hotel_a, day(10), day(13)))) == {'Free'}

    def test_type_missing_a_nightly_rate_excluded(self, hotel_a):
        make_room_type(hotel_a, name='Gap', prices=[Decimal('1000.00')] * 2)  # no rate for 3rd night
        make_room_type(hotel_a, name='Priced')
        assert set(as_dict(get_available_room_types(hotel_a, day(10), day(13)))) == {'Priced'}

    def test_type_without_rooms_excluded(self, hotel_a):
        make_room_type(hotel_a, rooms=0, name='Empty')
        assert list(get_available_room_types(hotel_a, day(10), day(13))) == []

    def test_runs_in_one_query(self, hotel_a, django_assert_num_queries):
        for i in range(3):
            make_room_type(hotel_a, rooms=2, name=f'Type {i}')
        with django_assert_num_queries(1):
            list(get_available_room_types(hotel_a, day(10), day(13)))


class TestValidation:
    @pytest.mark.parametrize('check_in, check_out', [(10, 10), (12, 10)])
    def test_check_out_must_be_after_check_in(self, hotel_a, check_in, check_out):
        with pytest.raises(ValidationError) as exc:
            get_available_room_types(hotel_a, day(check_in), day(check_out))
        assert 'check_out' in exc.value.message_dict

    def test_past_check_in_rejected(self, hotel_a):
        with pytest.raises(ValidationError) as exc:
            get_available_room_types(hotel_a, day(-1), day(2))
        assert 'check_in' in exc.value.message_dict

    def test_today_is_allowed(self, hotel_a):
        make_room_type(hotel_a, name='Deluxe', start=0)
        assert len(get_available_room_types(hotel_a, day(0), day(1))) == 1


class TestIsolation:
    def test_only_own_room_types_returned(self, hotel_a, hotel_b):
        make_room_type(hotel_a, name='A Room')
        make_room_type(hotel_b, name='B Room')
        assert set(as_dict(get_available_room_types(hotel_a, day(10), day(13)))) == {'A Room'}

    def test_other_property_bookings_and_rates_ignored(self, hotel_a, hotel_b):
        mine = make_room_type(hotel_a, name='Same Name', prices=[Decimal('1000.00')] * 3)
        theirs = make_room_type(hotel_b, name='Same Name', prices=[Decimal('9999.00')] * 3)
        book(theirs.rooms.get(), day(10), day(13))

        result = as_dict(get_available_room_types(hotel_a, day(10), day(13)))

        assert result == {'Same Name': (1, Decimal('3000.00'))}
        assert mine.property == hotel_a
