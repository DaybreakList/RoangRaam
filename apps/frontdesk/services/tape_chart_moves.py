"""Move one booked room before check-in, keeping its dates and price snapshots."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.bookings.models import Booking, BookingRoom
from apps.rooms.models import Room

ROOM_TAKEN_MESSAGE = 'ห้องที่เลือกไม่ว่างตลอดช่วงเข้าพัก กรุณาเลือกห้องอื่น'


def move_booking_room(property, booking_room_id, *, target_room_id):
    """Return (BookingRoom, changed). Foreign IDs/relations are 404, rules are validation errors.

    Lock Booking -> BookingRoom -> Rooms (pk order), as check-in/out do. Locking
    the parent also serializes cancellation and payment/expiry status changes.
    The caller checks membership/role; all data lookups here are property scoped.
    """
    # Import lazily: payments imports booking services and cancellation.
    from apps.bookings.payments import PAYMENT_WINDOW

    scoped_stays = BookingRoom.scoped.for_property(property).filter(
        booking__property=property, booking__guest__property=property,
        room__property=property, room__room_type__property=property,
        room_type__property=property,
    )
    with transaction.atomic():
        # Find the parent without locking a child before it (avoids lock inversion).
        parent_id = get_object_or_404(scoped_stays.only('booking_id'), pk=booking_room_id).booking_id
        booking = get_object_or_404(
            Booking.scoped.for_property(property).select_for_update(), pk=parent_id,
        )
        stay = get_object_or_404(
            scoped_stays.select_for_update(of=('self',)), pk=booking_room_id, booking=booking,
        )
        rooms = {
            room.pk: room for room in Room.scoped.for_property(property)
            .filter(pk__in={stay.room_id, target_room_id}, room_type__property=property)
            .select_for_update(of=('self',)).order_by('pk')
        }
        if stay.room_id not in rooms or target_room_id not in rooms:
            raise Http404
        target = rooms[target_room_id]
        if not stay.is_active or booking.status not in (
            Booking.Status.CONFIRMED, Booking.Status.PENDING_PAYMENT,
        ):
            raise ValidationError({'status': 'ย้ายห้องได้เฉพาะการจองที่ยังไม่เช็คอินและยังใช้งานอยู่'})
        # Match release_expired_bookings: the exact expiry instant is expired.
        if (booking.status == Booking.Status.PENDING_PAYMENT
                and booking.created_at <= timezone.now() - PAYMENT_WINDOW):
            raise ValidationError({'status': 'การจองนี้หมดเวลาชำระเงินแล้ว ไม่สามารถย้ายห้องได้'})
        if not target.is_active:
            raise ValidationError({'target_room_id': 'ห้องปลายทางปิดใช้งานอยู่'})
        if target.room_type_id != stay.room_type_id:
            raise ValidationError({'target_room_id': 'ต้องเลือกห้องประเภทเดียวกับที่จองไว้'})
        if target.pk == stay.room_id:
            return stay, False
        # Check the entire stored range, including nights outside the chart window.
        # Exclude only this row: another room in the same booking still blocks a move.
        if BookingRoom.scoped.for_property(property).filter(
            room=target, is_active=True, stay_range__overlap=stay.stay_range,
        ).exclude(pk=stay.pk).exists():
            raise ValidationError({'target_room_id': ROOM_TAKEN_MESSAGE})
        try:
            # A savepoint makes the transaction usable after the DB collision guard fires.
            with transaction.atomic():
                stay.room = target
                stay.save(update_fields=['room'])
        except IntegrityError as error:
            constraint = getattr(getattr(error.__cause__, 'diag', None), 'constraint_name', None)
            if constraint != 'bookings_bookingroom_no_overlap':
                raise
            raise ValidationError({'target_room_id': ROOM_TAKEN_MESSAGE}) from error
        return stay, True
