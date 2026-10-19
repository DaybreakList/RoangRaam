from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone
from psycopg.types.range import DateRange

from apps.bookings.models import BookingRoom
from apps.rooms.models import Room

WINDOW_DAYS = 14


def build_tape_chart(property, *, start=None, floor=None, room_type=None):
    """Read a property's fourteen-night window in a fixed number of queries."""
    start = start or timezone.localdate(timezone=ZoneInfo(property.timezone))
    end = start + timedelta(days=WINDOW_DAYS)
    stays = list(
        BookingRoom.scoped.for_property(property)
        .filter(is_active=True, stay_range__overlap=DateRange(start, end),
                booking__property=property, room__property=property,
                room_type__property=property, booking__guest__property=property)
        .select_related('booking__guest')
        .order_by('stay_range', 'pk')
    )
    rooms = (
        Room.scoped.for_property(property)
        .filter(Q(is_active=True) | Q(pk__in=[stay.room_id for stay in stays]),
                room_type__property=property)
        .select_related('room_type').order_by('floor', 'number', 'pk')
    )
    if floor is not None:
        rooms = rooms.filter(floor=floor)
    if room_type is not None:
        rooms = rooms.filter(room_type=room_type)
    by_room = {}
    for stay in stays:
        left = max(start, stay.stay_range.lower)
        right = min(end, stay.stay_range.upper)
        by_room.setdefault(stay.room_id, []).append({
            'code': stay.booking.code,
            'guest_name': str(stay.booking.guest),
            'status': stay.booking.status,
            'label': stay.booking.get_status_display(),
            'start': stay.stay_range.lower,
            'end': stay.stay_range.upper,
            'column': (left - start).days + 1,
            'span': (right - left).days,
            'continues_before': stay.stay_range.lower < start,
            'continues_after': stay.stay_range.upper > end,
        })
    return {
        'start': start, 'end': end,
        'last_day': end - timedelta(days=1),
        'days': [start + timedelta(days=i) for i in range(WINDOW_DAYS)],
        'rows': [{'room': room, 'bars': by_room.get(room.pk, [])} for room in rooms],
    }
