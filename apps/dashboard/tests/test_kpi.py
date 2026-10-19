from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.bookings.models import Booking
from apps.bookings.tests.factories import (
    BookingFactory,
    BookingNightRateFactory,
    BookingRoomFactory,
)
from apps.dashboard.services import kpi_summary
from apps.properties.tests.factories import PropertyFactory
from apps.rooms.tests.factories import RoomFactory

pytestmark = pytest.mark.django_db

START = date(2026, 11, 2)
END = date(2026, 11, 4)  # three nights: 2, 3 and 4 November


def book(room, nights, status=Booking.Status.CONFIRMED, active=True):
    """One booking of `room`; `nights` maps each night to its snapshot price."""
    booking = BookingFactory(
        property=room.property,
        status=status,
        check_in_date=min(nights),
        check_out_date=max(nights) + timedelta(days=1),
    )
    booking_room = BookingRoomFactory(booking=booking, room=room, is_active=active)
    for night, price in nights.items():
        BookingNightRateFactory(booking_room=booking_room, night=night, price=Decimal(price))
    return booking


@pytest.fixture
def hotels():
    """
    Hand-calculated data set. Hotel A has 7% VAT and two active rooms, so the
    three nights give 6 room-nights. Counted by default: 1070 + 1284 + 2140 = 4494
    with VAT, 4200 without.
    """
    hotel_a = PropertyFactory(tax_rate=Decimal('7.00'))
    room_1 = RoomFactory(property=hotel_a)
    room_2 = RoomFactory(property=hotel_a)
    closed_room = RoomFactory(property=hotel_a, is_active=False)

    book(room_1, {date(2026, 11, 2): '1070.00', date(2026, 11, 3): '1284.00'})
    # 5 November is outside the range
    book(room_2, {date(2026, 11, 4): '2140.00', date(2026, 11, 5): '2140.00'})
    book(room_2, {date(2026, 11, 2): '1070.00'}, status=Booking.Status.PENDING_PAYMENT)
    book(room_1, {date(2026, 11, 4): '1070.00'}, status=Booking.Status.CANCELLED, active=False)
    book(closed_room, {date(2026, 11, 3): '1070.00'})

    hotel_b = PropertyFactory(tax_rate=Decimal('7.00'))
    book(RoomFactory(property=hotel_b), {date(2026, 11, 3): '5350.00'})
    return hotel_a, hotel_b


def test_default_counts_paid_stays_without_vat(hotels):
    hotel_a, _ = hotels

    result = kpi_summary(hotel_a, START, END)

    assert result.nights == 3
    assert result.rooms_available == 6
    assert result.rooms_sold == 3
    assert result.room_revenue == Decimal('4200.00')
    assert result.occupancy == Decimal('50.00')
    assert result.adr == Decimal('1400.00')
    assert result.revpar == Decimal('700.00')


def test_include_vat_reports_snapshot_prices_as_booked(hotels):
    hotel_a, _ = hotels

    result = kpi_summary(hotel_a, START, END, include_vat=True)

    assert result.room_revenue == Decimal('4494.00')
    assert result.adr == Decimal('1498.00')
    assert result.revpar == Decimal('749.00')
    assert result.occupancy == Decimal('50.00')


def test_include_pending_counts_bookings_waiting_for_payment(hotels):
    hotel_a, _ = hotels

    result = kpi_summary(hotel_a, START, END, include_pending=True)

    assert result.rooms_sold == 4
    assert result.room_revenue == Decimal('5200.00')
    assert result.occupancy == Decimal('66.67')
    assert result.adr == Decimal('1300.00')
    assert result.revpar == Decimal('866.67')


def test_single_night_range(hotels):
    hotel_a, _ = hotels

    result = kpi_summary(hotel_a, date(2026, 11, 3), date(2026, 11, 3))

    assert result.nights == 1
    assert result.rooms_available == 2
    assert result.rooms_sold == 1
    assert result.room_revenue == Decimal('1200.00')
    assert result.occupancy == Decimal('50.00')
    assert result.adr == Decimal('1200.00')
    assert result.revpar == Decimal('600.00')


def test_other_hotel_data_is_never_counted(hotels):
    hotel_a, hotel_b = hotels

    result_a = kpi_summary(hotel_a, START, END)
    result_b = kpi_summary(hotel_b, START, END)

    # Hotel A's figures above already exclude hotel B's 5350 night
    assert result_a.room_revenue == Decimal('4200.00')
    assert result_b.rooms_available == 3
    assert result_b.rooms_sold == 1
    assert result_b.room_revenue == Decimal('5000.00')


def test_no_sales_gives_zero_occupancy_and_no_adr():
    hotel = PropertyFactory()
    RoomFactory(property=hotel)

    result = kpi_summary(hotel, START, END)

    assert result.rooms_sold == 0
    assert result.room_revenue == Decimal('0.00')
    assert result.occupancy == Decimal('0.00')
    assert result.adr is None
    assert result.revpar == Decimal('0.00')


def test_hotel_without_active_rooms_has_no_ratios():
    hotel = PropertyFactory()
    RoomFactory(property=hotel, is_active=False)

    result = kpi_summary(hotel, START, END)

    assert result.rooms_available == 0
    assert result.occupancy is None
    assert result.adr is None
    assert result.revpar is None


def test_end_before_start_is_rejected():
    hotel = PropertyFactory()

    with pytest.raises(ValidationError) as error:
        kpi_summary(hotel, END, START)

    assert 'end' in error.value.message_dict


def test_money_values_are_decimal(hotels):
    hotel_a, _ = hotels

    result = kpi_summary(hotel_a, START, END)

    for value in (result.room_revenue, result.occupancy, result.adr, result.revpar):
        assert isinstance(value, Decimal)


def test_query_count_does_not_grow_with_the_range(hotels, django_assert_num_queries):
    hotel_a, _ = hotels

    with django_assert_num_queries(2):
        kpi_summary(hotel_a, START, END)
    with django_assert_num_queries(2):
        kpi_summary(hotel_a, START - timedelta(days=60), END + timedelta(days=60))
