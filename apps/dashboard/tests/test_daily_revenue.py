from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.bookings.models import Booking
from apps.bookings.tests.factories import (
    BookingFactory,
    BookingNightRateFactory,
    BookingRoomFactory,
)
from apps.dashboard.services import daily_revenue, hotel_comparison, kpi_summary
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
def hotel():
    """7% VAT; every price below divides evenly, so 1070 is 1000 without VAT."""
    hotel = PropertyFactory(tax_rate=Decimal('7.00'))
    room_1 = RoomFactory(property=hotel)
    room_2 = RoomFactory(property=hotel)
    closed_room = RoomFactory(property=hotel, is_active=False)

    book(room_1, {date(2026, 11, 2): '1070.00', date(2026, 11, 4): '2140.00'})
    book(room_2, {date(2026, 11, 2): '1284.00'})
    # None of these are sold nights
    book(room_2, {date(2026, 11, 3): '1070.00'}, status=Booking.Status.PENDING_PAYMENT)
    book(room_1, {date(2026, 11, 3): '1070.00'}, status=Booking.Status.CANCELLED, active=False)
    book(closed_room, {date(2026, 11, 3): '1070.00'})
    book(room_1, {date(2026, 11, 5): '1070.00'})  # outside the range
    return hotel


def test_every_night_is_listed_and_nights_without_a_sale_are_zero(hotel):
    days = daily_revenue(hotel, START, END)

    assert [(day.night, day.rooms_sold, day.room_revenue) for day in days] == [
        (date(2026, 11, 2), 2, Decimal('2200.00')),
        (date(2026, 11, 3), 0, Decimal('0.00')),
        (date(2026, 11, 4), 1, Decimal('2000.00')),
    ]


def test_days_add_up_to_the_summary(hotel):
    days = daily_revenue(hotel, START, END)

    summary = kpi_summary(hotel, START, END)
    assert sum(day.room_revenue for day in days) == summary.room_revenue
    assert sum(day.rooms_sold for day in days) == summary.rooms_sold


def test_include_vat_reports_the_prices_as_booked(hotel):
    days = daily_revenue(hotel, START, END, include_vat=True)

    assert [day.room_revenue for day in days] == [
        Decimal('2354.00'), Decimal('0.00'), Decimal('2140.00'),
    ]


def test_include_pending_counts_unpaid_bookings(hotel):
    days = daily_revenue(hotel, START, END, include_pending=True)

    assert days[1].rooms_sold == 1
    assert days[1].room_revenue == Decimal('1000.00')


def test_other_hotels_are_never_counted(hotel):
    other = PropertyFactory(tax_rate=Decimal('7.00'))
    book(RoomFactory(property=other), {date(2026, 11, 3): '99999.00'})

    days = daily_revenue(hotel, START, END)

    assert days[1].rooms_sold == 0
    assert days[1].room_revenue == Decimal('0.00')


def test_end_before_start_is_rejected(hotel):
    with pytest.raises(ValidationError):
        daily_revenue(hotel, END, START)


def test_one_query_whatever_the_size_of_the_range(hotel):
    with CaptureQueriesContext(connection) as short:
        daily_revenue(hotel, START, END)
    with CaptureQueriesContext(connection) as long:
        days = daily_revenue(hotel, START, START + timedelta(days=364))

    assert len(short) == len(long) == 1
    assert len(days) == 365


def test_comparison_lists_hotels_by_name_with_their_own_numbers(hotel):
    other = PropertyFactory(name='A hotel that sorts first', tax_rate=Decimal('7.00'))
    hotel.name = 'Zulu hotel'
    hotel.save()
    book(RoomFactory(property=other), {date(2026, 11, 3): '5350.00'})

    rows = hotel_comparison([hotel, other], START, END)

    assert [row.property.name for row in rows] == ['A hotel that sorts first', 'Zulu hotel']
    assert rows[0].summary.room_revenue == Decimal('5000.00')
    assert rows[1].summary.room_revenue == Decimal('4200.00')
