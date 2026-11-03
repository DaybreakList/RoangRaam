"""
Today's front desk lists (TASK-40): arrivals, departures and guests in house.

"Today" is the property's own date, from Property.timezone, not the server's.
PENDING_PAYMENT bookings are left out: they only hold rooms for the payment window
(60 minutes) and the front desk cannot check them in. Cancelled bookings and
released rooms (BookingRoom.is_active=False) are never shown. Each row carries the
booking's payment status, worked out by apps.bookings.payment_status, and whether its
bill is closed (bill_closed), so the check-out button can be disabled before the click.
"""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from django.db.models import Prefetch, Q
from django.utils import timezone

from apps.bookings.models import Booking, BookingRoom, Receipt
from apps.bookings.payment_status import PaymentStatus, payment_statuses

# Arrivals keep guests who already checked in today, so the desk sees who is still to come
ARRIVAL_STATUSES = (Booking.Status.CONFIRMED, Booking.Status.CHECKED_IN)
DEPARTURE_STATUSES = (Booking.Status.CHECKED_IN, Booking.Status.CHECKED_OUT)


@dataclass(frozen=True)
class DailyRow:
    booking: Booking
    room_numbers: list
    room_type_names: list
    payment_status: PaymentStatus
    bill_closed: bool

    @property
    def sort_key(self):
        return (self.room_numbers[0] if self.room_numbers else '', self.booking.code)


def property_today(property):
    return timezone.localdate(timezone=ZoneInfo(property.timezone))


def daily_lists(property, today=None):
    """
    Arrivals, departures and in-house bookings of `property` for its own today.

    Runs four queries however many bookings there are: one for the bookings with
    their guests, one for their active rooms, one for their payments and one for
    their receipts. When nothing is listed only the first runs: payments and
    receipts are not queried.
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
    bookings = list(bookings)
    statuses = payment_statuses(property, bookings)
    closed_ids = _closed_bill_ids(property, bookings)
    rows = sorted(
        (_row(booking, statuses[booking.pk], booking.pk in closed_ids) for booking in bookings),
        key=lambda row: row.sort_key,
    )
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


def _closed_bill_ids(property, bookings):
    """
    The pks of `bookings` whose bill is closed, from one query on their receipts only.

    Same rule as bill_is_closed() in apps/bookings/folio.py, for many bookings at once:
    a booking with a Receipt has its bill closed. Keep the two in step.
    """
    if not bookings:
        return set()
    return set(
        Receipt.scoped.for_property(property)
        .filter(booking__in=bookings)
        .values_list('booking_id', flat=True)
    )


def _row(booking, payment_status, bill_closed):
    rooms = booking.active_rooms
    type_names = []
    for booking_room in rooms:
        if booking_room.room_type.name not in type_names:
            type_names.append(booking_room.room_type.name)
    return DailyRow(
        booking=booking,
        room_numbers=[booking_room.room.number for booking_room in rooms],
        room_type_names=type_names,
        payment_status=payment_status,
        bill_closed=bill_closed,
    )
