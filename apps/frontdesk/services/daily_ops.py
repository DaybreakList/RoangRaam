"""
Today's front desk lists (TASK-40): arrivals, departures and guests in house.

"Today" is the property's own date, from Property.timezone, not the server's.
PENDING_PAYMENT bookings are left out: they hold rooms for 30 minutes only and
the front desk cannot check them in. Cancelled bookings and released rooms
(BookingRoom.is_active=False) are never shown.
"""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.bookings.models import Booking, BookingRoom

# Arrivals keep guests who already checked in today, so the desk sees who is still to come
ARRIVAL_STATUSES = (Booking.Status.CONFIRMED, Booking.Status.CHECKED_IN)
DEPARTURE_STATUSES = (Booking.Status.CHECKED_IN, Booking.Status.CHECKED_OUT)


@dataclass(frozen=True)
class DailyRow:
    booking: Booking
    room_numbers: list
    room_type_names: list

    @property
    def sort_key(self):
        return (self.room_numbers[0] if self.room_numbers else '', self.booking.code)


def property_today(property):
    return timezone.localdate(timezone=ZoneInfo(property.timezone))


def daily_lists(property, today=None):
    """
    Arrivals, departures and in-house bookings of `property` for its own today.

    Runs two queries however many bookings there are: one for the bookings with
    their guests, one for their active rooms.
    """
    today = today or property_today(property)
    bookings = (
        Booking.scoped.for_property(property)
        .filter(
            Q(check_in_date=today, status__in=ARRIVAL_STATUSES)
            | Q(check_out_date=today, status__in=DEPARTURE_STATUSES)
            | Q(status=Booking.Status.CHECKED_IN),
            guest__property=property,
        )
        .select_related('guest')
        .prefetch_related(Prefetch(
            'booking_rooms',
            queryset=BookingRoom.scoped.for_property(property)
            .filter(is_active=True, room__property=property)
            .select_related('room', 'room_type')
            .order_by('room__number', 'pk'),
            to_attr='active_rooms',
        ))
    )
    rows = sorted((_row(booking) for booking in bookings), key=lambda row: row.sort_key)
    return {
        'today': today,
        'arrivals': [
            row for row in rows
            if row.booking.check_in_date == today and row.booking.status in ARRIVAL_STATUSES
        ],
        'departures': [
            row for row in rows
            if row.booking.check_out_date == today and row.booking.status in DEPARTURE_STATUSES
        ],
        'in_house': [row for row in rows if row.booking.status == Booking.Status.CHECKED_IN],
    }


def _row(booking):
    rooms = booking.active_rooms
    type_names = []
    for booking_room in rooms:
        if booking_room.room_type.name not in type_names:
            type_names.append(booking_room.room_type.name)
    return DailyRow(
        booking=booking,
        room_numbers=[booking_room.room.number for booking_room in rooms],
        room_type_names=type_names,
    )
