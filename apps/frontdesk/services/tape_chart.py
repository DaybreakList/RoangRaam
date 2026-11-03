from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from psycopg.types.range import DateRange

from apps.bookings.models import Booking, BookingRoom
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
    rooms = list(rooms)
    visible_ids = {room.pk for room in rooms}
    stays = [stay for stay in stays if stay.room_id in visible_ids]
    now = timezone.now()
    from apps.bookings.payments import PAYMENT_WINDOW

    by_room = {}
    move_stays = {}
    for stay in stays:
        left = max(start, stay.stay_range.lower)
        right = min(end, stay.stay_range.upper)
        expires = (stay.booking.created_at + PAYMENT_WINDOW
                   if stay.booking.status == Booking.Status.PENDING_PAYMENT else None)
        movable = (stay.booking.status == Booking.Status.CONFIRMED
                   or (expires is not None and expires > now))
        move_stays[str(stay.pk)] = {
            'id': str(stay.pk), 'roomId': str(stay.room_id), 'typeId': str(stay.room_type_id),
            'start': stay.stay_range.lower.isoformat(), 'end': stay.stay_range.upper.isoformat(),
            'canMove': movable, 'expiresAt': expires.isoformat() if expires else None,
            'url': reverse('tape_chart:move', kwargs={
                'property_slug': property.slug, 'booking_room_id': stay.pk,
            }),
        }
        by_room.setdefault(stay.room_id, []).append({
            'booking_room_id': str(stay.pk), 'can_move': movable,
            'booking_id': stay.booking.pk,
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
    move_rooms = {str(room.pk): {
        'id': str(room.pk), 'typeId': str(room.room_type_id), 'active': room.is_active,
        'number': room.number, 'stays': [],
    } for room in rooms}
    movable_stays = [stay for stay in stays if move_stays[str(stay.pk)]['canMove']]
    if movable_stays:
        # Include collisions beyond the visible window for clipped booking bars.
        bounds = DateRange(min(stay.stay_range.lower for stay in movable_stays),
                           max(stay.stay_range.upper for stay in movable_stays))
        held = BookingRoom.scoped.for_property(property).filter(
            room_id__in=visible_ids, is_active=True, stay_range__overlap=bounds,
            booking__property=property, room__property=property, room_type__property=property,
        ).values_list('pk', 'room_id', 'stay_range')
        for pk, room_id, span in held:
            move_rooms[str(room_id)]['stays'].append({
                'id': str(pk), 'start': span.lower.isoformat(), 'end': span.upper.isoformat(),
            })
    return {
        'move_data': {'serverNow': now.isoformat(), 'rooms': move_rooms, 'stays': move_stays},
        'start': start, 'end': end,
        'last_day': end - timedelta(days=1),
        'days': [start + timedelta(days=i) for i in range(WINDOW_DAYS)],
        'rows': [{'room': room, 'bars': by_room.get(room.pk, [])} for room in rooms],
    }
