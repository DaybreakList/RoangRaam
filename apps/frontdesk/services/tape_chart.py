from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format
from psycopg.types.range import DateRange

from apps.bookings.models import Booking, BookingRoom
from apps.rooms.models import Room

WINDOW_DAYS = 14
ALLOWED_WINDOW_DAYS = (14, 60)
INITIAL_ROWS = 8
ROW_HEIGHT = 144
HEADER_HEIGHT = 64


def build_tape_chart(property, *, start=None, floor=None, room_type=None, days=WINDOW_DAYS):
    """Read a bounded property window in a fixed number of queries."""
    if days not in ALLOWED_WINDOW_DAYS:
        raise ValueError("Unsupported chart window")
    start = start or timezone.localdate(timezone=ZoneInfo(property.timezone))
    end = start + timedelta(days=days)
    stays = (
        BookingRoom.scoped.for_property(property)
        .filter(is_active=True, stay_range__overlap=DateRange(start, end),
                booking__property=property, room__property=property,
                room_type__property=property, booking__guest__property=property)
        .values('pk', 'room_id', 'room_type_id', 'stay_range', 'booking_id',
                'booking__code', 'booking__status', 'booking__created_at',
                'booking__guest__first_name', 'booking__guest__last_name')
        .order_by('stay_range', 'pk')
    )
    # Apply display filters in SQL before loading booking/guest columns.
    if floor is not None:
        stays = stays.filter(room__floor=floor)
    if room_type is not None:
        stays = stays.filter(room__room_type=room_type)
    stays_query = stays
    stays = list(stays_query)
    rooms = (
        Room.scoped.for_property(property)
        .filter(Q(is_active=True) | Q(pk__in=[stay['room_id'] for stay in stays]),
                room_type__property=property)
        .select_related('room_type').order_by('floor', 'number', 'pk')
    )
    if floor is not None:
        rooms = rooms.filter(floor=floor)
    if room_type is not None:
        rooms = rooms.filter(room_type=room_type)
    rooms = list(rooms)
    visible_ids = {room.pk for room in rooms}
    stays = [stay for stay in stays if stay['room_id'] in visible_ids]
    now = timezone.now()
    from apps.bookings.payments import PAYMENT_WINDOW

    labels = dict(Booking.Status.choices)
    # Dates repeat across thousands of bars; format each distinct date once per response.
    formatted_dates = {day: date_format(day, 'j M Y')
                       for day in {day for stay in stays
                                   for day in (stay['stay_range'].lower, stay['stay_range'].upper)}}
    by_room = {}
    move_stays = {}
    for stay in stays:
        left = max(start, stay['stay_range'].lower)
        right = min(end, stay['stay_range'].upper)
        expires = (stay['booking__created_at'] + PAYMENT_WINDOW
                   if stay['booking__status'] == Booking.Status.PENDING_PAYMENT else None)
        movable = (stay['booking__status'] == Booking.Status.CONFIRMED
                   or (expires is not None and expires > now))
        move_stays[str(stay['pk'])] = {
            'id': str(stay['pk']), 'roomId': str(stay['room_id']), 'typeId': str(stay['room_type_id']),
            'start': stay['stay_range'].lower.isoformat(), 'end': stay['stay_range'].upper.isoformat(),
            'canMove': movable, 'expiresAt': expires.isoformat() if expires else None,
            'url': reverse('tape_chart:move', kwargs={
                'property_slug': property.slug, 'booking_room_id': stay['pk'],
            }),
        }
        bar = {
            'booking_room_id': str(stay['pk']), 'can_move': movable,
            'booking_id': stay['booking_id'],
            'code': stay['booking__code'],
            'guest_name': f"{stay['booking__guest__first_name']} {stay['booking__guest__last_name']}",
            'status': stay['booking__status'],
            'label': labels[stay['booking__status']],
            'start': stay['stay_range'].lower,
            'end': stay['stay_range'].upper,
            'start_label': formatted_dates[stay['stay_range'].lower],
            'end_label': formatted_dates[stay['stay_range'].upper],
            'column': (left - start).days + 1,
            'span': (right - left).days,
            'continues_before': stay['stay_range'].lower < start,
            'continues_after': stay['stay_range'].upper > end,
        }
        by_room.setdefault(stay['room_id'], []).append(bar)
        move_stays[str(stay['pk'])]['display'] = {
            'bookingId': str(stay['booking_id']), 'code': bar['code'], 'guest': bar['guest_name'],
            'status': bar['status'], 'column': bar['column'], 'span': bar['span'],
            'before': bar['continues_before'], 'after': bar['continues_after'],
        }
    move_rooms = {str(room.pk): {
        'id': str(room.pk), 'typeId': str(room.room_type_id), 'active': room.is_active,
        'number': room.number, 'stays': [],
        'floor': room.floor, 'typeName': room.room_type.name,
        'hkStatus': room.hk_status, 'hkLabel': room.get_hk_status_display(),
        'bars': [bar['booking_room_id'] for bar in by_room.get(room.pk, [])],
    } for room in rooms}
    movable_stays = [stay for stay in stays if move_stays[str(stay['pk'])]['canMove']]
    if movable_stays:
        # Include collisions beyond the visible window for clipped booking bars.
        bounds = DateRange(min(stay['stay_range'].lower for stay in movable_stays),
                           max(stay['stay_range'].upper for stay in movable_stays))
        for stay in stays:
            span = stay['stay_range']
            if span.lower < bounds.upper and bounds.lower < span.upper:
                move_rooms[str(stay['room_id'])]['stays'].append({
                    'id': str(stay['pk']), 'start': span.lower.isoformat(), 'end': span.upper.isoformat(),
                })
        held = BookingRoom.scoped.for_property(property).filter(
            room_id__in=visible_ids, is_active=True, stay_range__overlap=bounds,
            booking__property=property, room__property=property, room_type__property=property,
        ).exclude(pk__in=stays_query.order_by().values('pk')).values_list('pk', 'room_id', 'stay_range')
        for pk, room_id, span in held:
            move_rooms[str(room_id)]['stays'].append({
                'id': str(pk), 'start': span.lower.isoformat(), 'end': span.upper.isoformat(),
            })
    rows = [{'room': room, 'bars': by_room.get(room.pk, [])} for room in rooms]
    return {
        'move_data': {
            'serverNow': now.isoformat(), 'rooms': move_rooms, 'stays': move_stays,
            'order': [str(room.pk) for room in rooms], 'labels': labels,
            'dates': {day.isoformat(): label for day, label in formatted_dates.items()},
            'viewport': {'rowHeight': ROW_HEIGHT, 'headerHeight': HEADER_HEIGHT, 'overscan': 3},
        },
        'start': start, 'end': end, 'window_days': days,
        'last_day': end - timedelta(days=1),
        'days': [start + timedelta(days=i) for i in range(days)],
        'rows': rows, 'initial_rows': rows[:INITIAL_ROWS],
        'row_height': ROW_HEIGHT, 'header_height': HEADER_HEIGHT,
        'remaining_height': max(0, len(rows) - INITIAL_ROWS) * ROW_HEIGHT,
    }
