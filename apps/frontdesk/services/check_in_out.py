"""
Check-in and check-out at the front desk (TASK-43, TASK-44).

Rules agreed by the team for Sprint 3:
- Check-in only on the booking's check_in_date, in the property's own timezone.
- Every room must be active and Clean. A room may be swapped for another active,
  Clean room of the same type that is free for the whole stay; the price stays as
  booked. The exclusion constraint on BookingRoom stays the last line of defence.
- Check-out any day after check_in_date. Leaving early does not change the price,
  and the nights left stay held: releasing them would edit the booking, which is
  out of scope (AGENTS.md) and would need a refund decision.
- Check-out marks every room of the booking Dirty.

Ids sent by the client that are not in this booking or this property are 404,
like create_booking does for room types, and nothing is changed.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.bookings.models import Booking, BookingRoom, Guest
from apps.bookings.services import booked_room_ids
from apps.rooms.models import Room

from .daily_ops import property_today

ROOM_TAKEN_MESSAGE = 'ห้องที่เลือกไม่ว่างในช่วงวันที่เข้าพักแล้ว กรุณาเลือกห้องอื่น'


def check_in(property, booking_id, *, user, id_type, id_number, nationality='', room_changes=None,
             today=None):
    """
    Check in every active room of a CONFIRMED booking of `property`.

    `room_changes` maps a BookingRoom id to the Room id the guest gets instead.
    The guest's ID document is saved on the Guest. Raises ValidationError when a
    rule is broken, keyed by 'status', 'check_in_date', 'id_number' or 'rooms'.
    """
    room_changes = room_changes or {}
    id_number = (id_number or '').strip()
    if id_type not in Guest.IdType.values or not id_number:
        raise ValidationError({'id_number': 'กรุณากรอกประเภทและเลขที่บัตรประชาชนหรือหนังสือเดินทาง'})

    with transaction.atomic():
        booking = _lock_booking(property, booking_id)
        if booking.status != Booking.Status.CONFIRMED:
            raise ValidationError({
                'status': f'เช็คอินไม่ได้ เพราะการจองมีสถานะ "{booking.get_status_display()}"',
            })
        today = today or property_today(property)
        if booking.check_in_date != today:
            raise ValidationError({'check_in_date': 'เช็คอินได้เฉพาะในวันเช็คอินของการจองเท่านั้น'})

        booking_rooms = _lock_booking_rooms(property, booking)
        if set(room_changes) - {booking_room.pk for booking_room in booking_rooms}:
            raise Http404
        rooms = _lock_rooms(property, booking_rooms, room_changes)

        targets = {}
        for booking_room in booking_rooms:
            room = rooms[room_changes.get(booking_room.pk, booking_room.room_id)]
            _check_room(booking_room, room)
            if room.pk in targets.values():
                raise ValidationError({'rooms': f'เลือกห้อง {room.number} ซ้ำกันในการจองเดียว'})
            targets[booking_room.pk] = room.pk

        _move_rooms(property, booking, booking_rooms, rooms, targets)

        guest = booking.guest
        guest.id_type, guest.id_number = id_type, id_number
        guest.nationality = (nationality or '').strip()
        guest.save(update_fields=['id_type', 'id_number', 'nationality'])

        now = timezone.now()
        for booking_room in booking_rooms:
            booking_room.checked_in_at, booking_room.checked_in_by = now, user
            booking_room.save(update_fields=['checked_in_at', 'checked_in_by'])
        booking.status = Booking.Status.CHECKED_IN
        booking.save(update_fields=['status'])
    return booking


def check_out(property, booking_id, *, today=None):
    """
    Check out a CHECKED_IN booking of `property` and mark its rooms Dirty.

    Raises ValidationError keyed by 'status' or 'check_out_date'.
    """
    with transaction.atomic():
        booking = _lock_booking(property, booking_id)
        if booking.status != Booking.Status.CHECKED_IN:
            raise ValidationError({
                'status': f'เช็คเอาต์ไม่ได้ เพราะการจองมีสถานะ "{booking.get_status_display()}"',
            })
        today = today or property_today(property)
        if today <= booking.check_in_date:
            raise ValidationError({'check_out_date': 'เช็คเอาต์ได้ตั้งแต่วันถัดจากวันเช็คอิน'})

        booking_rooms = _lock_booking_rooms(property, booking)
        BookingRoom.scoped.for_property(property).filter(
            pk__in=[booking_room.pk for booking_room in booking_rooms],
        ).update(checked_out_at=timezone.now())
        # Room data is saved one by one: RoomDataQuerySet refuses update() to keep validation
        rooms = Room.scoped.for_property(property).filter(
            pk__in=[booking_room.room_id for booking_room in booking_rooms],
        ).select_for_update().order_by('pk')
        for room in rooms:
            room.hk_status = Room.HousekeepingStatus.DIRTY
            room.save(update_fields=['hk_status'])
        booking.status = Booking.Status.CHECKED_OUT
        booking.save(update_fields=['status'])
    return booking


def _lock_booking(property, booking_id):
    # Lock the row so a double click, or a check-in racing a cancel, cannot both pass
    return get_object_or_404(
        Booking.scoped.for_property(property).select_for_update(of=('self',)).select_related('guest'),
        pk=booking_id,
    )


def _lock_booking_rooms(property, booking):
    booking_rooms = list(
        BookingRoom.scoped.for_property(property)
        .filter(booking=booking, is_active=True)
        .select_for_update()
        .order_by('pk')
    )
    if not booking_rooms:
        raise ValidationError({'rooms': 'การจองนี้ไม่มีห้องที่ใช้งานอยู่'})
    return booking_rooms


def _lock_rooms(property, booking_rooms, room_changes):
    """Lock the booked and the requested rooms in pk order. Ids of another property are 404."""
    wanted = {booking_room.room_id for booking_room in booking_rooms} | set(room_changes.values())
    rooms = {
        room.pk: room
        for room in Room.scoped.for_property(property).filter(pk__in=wanted).select_for_update().order_by('pk')
    }
    if len(rooms) != len(wanted):
        raise Http404
    return rooms


def _check_room(booking_room, room):
    if room.room_type_id != booking_room.room_type_id:
        raise ValidationError({'rooms': f'ห้อง {room.number} ไม่ใช่ประเภทห้องที่จองไว้'})
    if not room.is_active:
        raise ValidationError({'rooms': f'ห้อง {room.number} ปิดใช้งานอยู่'})
    if room.hk_status != Room.HousekeepingStatus.CLEAN:
        raise ValidationError({'rooms': f'ห้อง {room.number} ยังไม่ได้ทำความสะอาด'})


def _move_rooms(property, booking, booking_rooms, rooms, targets):
    moving = [booking_room for booking_room in booking_rooms if targets[booking_room.pk] != booking_room.room_id]
    if not moving:
        return
    taken = set(
        booked_room_ids(property, booking.check_in_date, booking.check_out_date)
        .exclude(booking=booking)
        .values_list('room_id', flat=True)
    )
    for booking_room in moving:
        if targets[booking_room.pk] in taken:
            raise ValidationError({'rooms': ROOM_TAKEN_MESSAGE})
    try:
        # Savepoint: a swap inside this booking or a race the check above missed
        # is caught by the exclusion constraint without breaking the transaction
        with transaction.atomic():
            for booking_room in moving:
                booking_room.room = rooms[targets[booking_room.pk]]
                booking_room.save(update_fields=['room'])
    except IntegrityError:
        raise ValidationError({'rooms': ROOM_TAKEN_MESSAGE}) from None
