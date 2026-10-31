"""
KPI queries for the hotel dashboard (TASK-41): Occupancy, ADR and RevPAR.

Revenue comes from BookingNightRate, the price snapshot taken when each night was
booked, so later DailyRate changes never rewrite past results. Only rooms that are
active now are counted, on both sides of every ratio.

Two definitions still wait for the team and are therefore options:
- include_pending: count PENDING_PAYMENT bookings as sold (default: no, they are
  unpaid and only hold their rooms for the payment window, 60 minutes)
- include_vat: report revenue with VAT (default: no; prices include VAT, so it is
  removed with Property.tax_rate)
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db.models import Count, Sum

from apps.bookings.models import Booking, BookingNightRate
from apps.properties.models import Property
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


@dataclass(frozen=True)
class DailyRevenue:
    night: date
    rooms_sold: int
    room_revenue: Decimal


@dataclass(frozen=True)
class HotelKpi:
    property: Property
    summary: KpiSummary


def _nights_in(start, end):
    if end < start:
        raise ValidationError({'end': 'วันสิ้นสุดต้องไม่ก่อนวันเริ่มต้น'})
    return (end - start).days + 1


def _sold_night_rates(property, start, end, include_pending):
    statuses = SOLD_STATUSES + ((Booking.Status.PENDING_PAYMENT,) if include_pending else ())
    return BookingNightRate.scoped.for_property(property).filter(
        night__range=(start, end),
        booking_room__is_active=True,
        booking_room__room__is_active=True,
        booking_room__booking__status__in=statuses,
    )


def _revenue_as_reported(revenue, property, include_vat):
    return revenue if include_vat else revenue * 100 / (100 + property.tax_rate)


def kpi_summary(property, start, end, *, include_pending=False, include_vat=False):
    """
    Occupancy, ADR and RevPAR of `property` for the nights `start` to `end`, both
    included. Runs two queries whatever the size of the range.
    """
    nights = _nights_in(start, end)

    active_rooms = Room.scoped.for_property(property).filter(is_active=True).count()
    sold = _sold_night_rates(property, start, end, include_pending).aggregate(
        rooms_sold=Count('pk'), revenue=Sum('price'),
    )

    rooms_available = active_rooms * nights
    rooms_sold = sold['rooms_sold']
    revenue = _revenue_as_reported(sold['revenue'] or Decimal('0.00'), property, include_vat)

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


def daily_revenue(property, start, end, *, include_pending=False, include_vat=False):
    """
    Room revenue and rooms sold for every night from `start` to `end`, both included,
    with the same definitions as kpi_summary. Nights without a sale are listed with
    zero, so a chart has no gaps. One query whatever the size of the range. Each day
    is rounded on its own, so the days can differ from kpi_summary's total by cents.
    """
    nights = _nights_in(start, end)
    rows = (
        _sold_night_rates(property, start, end, include_pending)
        .values('night')
        .annotate(rooms_sold=Count('pk'), revenue=Sum('price'))
        .order_by('night')
    )
    by_night = {row['night']: row for row in rows}

    days = []
    for offset in range(nights):
        night = start + timedelta(days=offset)
        row = by_night.get(night)
        revenue = _revenue_as_reported(row['revenue'], property, include_vat) if row else Decimal('0')
        days.append(DailyRevenue(
            night=night,
            rooms_sold=row['rooms_sold'] if row else 0,
            room_revenue=_round(revenue),
        ))
    return days


def hotel_comparison(properties, start, end, **options):
    """
    kpi_summary of each of `properties`, ordered by hotel name. The caller decides
    which hotels may be compared; two queries per hotel.
    """
    return [
        HotelKpi(property=hotel, summary=kpi_summary(hotel, start, end, **options))
        for hotel in sorted(properties, key=lambda hotel: hotel.name)
    ]
