"""
KPI queries for the hotel dashboard (TASK-41): Occupancy, ADR and RevPAR.

Revenue comes from BookingNightRate, the price snapshot taken when each night was
booked, so later DailyRate changes never rewrite past results. Only rooms that are
active now are counted, on both sides of every ratio.

Two definitions still wait for the team and are therefore options:
- include_pending: count PENDING_PAYMENT bookings as sold (default: no, they are
  unpaid and only hold their rooms for 30 minutes)
- include_vat: report revenue with VAT (default: no; prices include VAT, so it is
  removed with Property.tax_rate)
"""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db.models import Count, Sum

from apps.bookings.models import Booking, BookingNightRate
from apps.rooms.models import Room

CENT = Decimal('0.01')
SOLD_STATUSES = (
    Booking.Status.CONFIRMED,
    Booking.Status.CHECKED_IN,
    Booking.Status.CHECKED_OUT,
)


@dataclass(frozen=True)
class KpiSummary:
    start: date
    end: date
    nights: int
    rooms_available: int
    rooms_sold: int
    room_revenue: Decimal
    occupancy: Decimal | None  # percent, e.g. Decimal('72.50')
    adr: Decimal | None  # None when no room was sold
    revpar: Decimal | None  # None when the hotel has no active room


def _round(value):
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def kpi_summary(property, start, end, *, include_pending=False, include_vat=False):
    """
    Occupancy, ADR and RevPAR of `property` for the nights `start` to `end`, both
    included. Runs two queries whatever the size of the range.
    """
    if end < start:
        raise ValidationError({'end': 'วันสิ้นสุดต้องไม่ก่อนวันเริ่มต้น'})
    nights = (end - start).days + 1

    active_rooms = Room.scoped.for_property(property).filter(is_active=True).count()
    statuses = SOLD_STATUSES + ((Booking.Status.PENDING_PAYMENT,) if include_pending else ())
    sold = BookingNightRate.scoped.for_property(property).filter(
        night__range=(start, end),
        booking_room__is_active=True,
        booking_room__room__is_active=True,
        booking_room__booking__status__in=statuses,
    ).aggregate(rooms_sold=Count('pk'), revenue=Sum('price'))

    rooms_available = active_rooms * nights
    rooms_sold = sold['rooms_sold']
    revenue = sold['revenue'] or Decimal('0.00')
    if not include_vat:
        revenue = revenue * 100 / (100 + property.tax_rate)

    # Ratios use the unrounded revenue so rounding happens once per figure
    return KpiSummary(
        start=start,
        end=end,
        nights=nights,
        rooms_available=rooms_available,
        rooms_sold=rooms_sold,
        room_revenue=_round(revenue),
        occupancy=_round(Decimal(rooms_sold) * 100 / rooms_available) if rooms_available else None,
        adr=_round(revenue / rooms_sold) if rooms_sold else None,
        revpar=_round(revenue / rooms_available) if rooms_available else None,
    )
