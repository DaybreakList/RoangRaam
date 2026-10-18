from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .models import Booking, BookingRoom

# Once a guest has checked in, the stay is handled at the front desk instead.
CANCELLABLE_STATUSES = {Booking.Status.PENDING_PAYMENT, Booking.Status.CONFIRMED}


def cancel_booking(property, booking_id):
    """
    Cancel a booking of `property` without a fee and release its rooms.

    Raises Http404 when the booking belongs to another property, like
    create_booking does. Raises ValidationError keyed by 'status' when the
    booking is no longer cancellable, so callers can read message_dict.
    """
    with transaction.atomic():
        # Lock the row so a double click, or a cancel racing a check-in,
        # cannot both pass the status check below.
        booking = get_object_or_404(
            Booking.scoped.for_property(property).select_for_update(),
            pk=booking_id,
        )
        if booking.status not in CANCELLABLE_STATUSES:
            raise ValidationError({
                'status': f'ไม่สามารถยกเลิกการจองที่มีสถานะ "{booking.get_status_display()}" ได้',
            })

        booking.status = Booking.Status.CANCELLED
        booking.cancelled_at = timezone.now()
        booking.save(update_fields=['status', 'cancelled_at'])

        # Inactive rows are ignored by the exclusion constraint and the
        # availability search, so the rooms can be sold again right away.
        BookingRoom.scoped.for_property(property).filter(booking=booking).update(is_active=False)
    return booking