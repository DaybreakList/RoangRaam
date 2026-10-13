from django.core.exceptions import ValidationError
from django.db.models import Count, DecimalField, IntegerField, OuterRef, Q, Subquery, Sum
from django.utils import timezone
from psycopg.types.range import DateRange

from apps.rooms.models import DailyRate, RoomType

from .models import BookingRoom


def validate_stay_dates(check_in, check_out):
    errors = {}
    if check_in < timezone.localdate():
        errors['check_in'] = 'วันเช็คอินต้องไม่ใช่วันที่ผ่านมาแล้ว'
    if check_out <= check_in:
        errors['check_out'] = 'วันเช็คเอาต์ต้องอยู่หลังวันเช็คอิน'
    if errors:
        raise ValidationError(errors)


def booked_room_ids(property, check_in, check_out):
    """Rooms held by an active booking that overlaps [check_in, check_out)."""
    return BookingRoom.scoped.for_property(property).filter(
        is_active=True,
        stay_range__overlap=DateRange(check_in, check_out),
    ).values('room_id')


def get_available_room_types(property, check_in, check_out):
    """
    Room types of `property` that still have a free room for every night
    of [check_in, check_out).

    Each result is annotated with:
      - available_count: active rooms of this type with no overlapping booking
      - total_price: sum of each night's DailyRate (per-night, never rate x nights)

    Room types missing a DailyRate for any night are excluded because they
    cannot be priced. Runs as a single query.
    """
    validate_stay_dates(check_in, check_out)
    nights = (check_out - check_in).days

    rates = (
        DailyRate.scoped.for_property(property)
        .filter(room_type=OuterRef('pk'), date__gte=check_in, date__lt=check_out)
        .order_by()
        .values('room_type')
    )

    return (
        RoomType.scoped.for_property(property)
        .annotate(
            available_count=Count(
                'rooms',
                filter=Q(rooms__is_active=True)
                & ~Q(rooms__id__in=booked_room_ids(property, check_in, check_out)),
                distinct=True,
            ),
            priced_nights=Subquery(
                rates.annotate(n=Count('pk')).values('n'), output_field=IntegerField(),
            ),
            total_price=Subquery(
                rates.annotate(total=Sum('price')).values('total'),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )
        .filter(available_count__gt=0, priced_nights=nights)
        .order_by('name', 'pk')
    )
