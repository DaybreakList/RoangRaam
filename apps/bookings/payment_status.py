"""
Payment status of bookings for staff screens, read from Booking and its Payment rows.

Nothing is stored: the status follows from Booking.status, Booking.payment_option
and the money left after refunds (CHARGE total minus REFUND total).
"""

from decimal import Decimal

from django.db import models
from django.db.models import Q, Sum

from .models import Booking, Payment


class PaymentStatus(models.TextChoices):
    PAID = 'PAID', 'จ่ายแล้ว'
    AWAITING = 'AWAITING', 'รอชำระ'
    PAY_AT_HOTEL = 'PAY_AT_HOTEL', 'จ่ายที่โรงแรม'
    REFUND_PENDING = 'REFUND_PENDING', 'รอคืนเงิน'
    REFUNDED = 'REFUNDED', 'คืนเงินแล้ว'
    NONE = 'NONE', 'ไม่มีการชำระ'


def payment_statuses(property, bookings):
    """
    Return {booking.pk: PaymentStatus} for `bookings` of `property`.

    `bookings` are Booking instances the caller already loaded, so their status
    and payment_option cost nothing more. Runs one query for any number of
    bookings and none for an empty list. Raises ValueError when a booking
    belongs to another property, since that means the caller's own query
    leaked across hotels.
    """
    bookings = list(bookings)
    if not bookings:
        return {}
    foreign = [str(booking.pk) for booking in bookings if booking.property_id != property.pk]
    if foreign:
        raise ValueError(f'Bookings {", ".join(foreign)} do not belong to property {property.pk}')

    totals = (
        Payment.scoped.for_property(property)
        .filter(booking__in=[booking.pk for booking in bookings])
        .order_by()
        .values('booking_id')
        .annotate(
            charged=Sum('amount', filter=Q(kind=Payment.Kind.CHARGE)),
            refunded=Sum('amount', filter=Q(kind=Payment.Kind.REFUND)),
        )
    )
    money = {
        row['booking_id']: (row['charged'] or Decimal('0'), row['refunded'] or Decimal('0'))
        for row in totals
    }
    return {
        booking.pk: _status(booking, *money.get(booking.pk, (Decimal('0'), Decimal('0'))))
        for booking in bookings
    }


def _status(booking, charged, refunded):
    kept = charged - refunded
    if booking.status == Booking.Status.CANCELLED:
        # Money taken after the booking was cancelled is refunded by the webhook;
        # until that REFUND row exists the guest is still owed it
        if kept > 0:
            return PaymentStatus.REFUND_PENDING
        if refunded > 0:
            return PaymentStatus.REFUNDED
        return PaymentStatus.NONE
    # Shown even after check-out until the folio records payments at the hotel
    if booking.payment_option == Booking.PaymentOption.PAY_AT_HOTEL:
        # Paid once the folio has taken the whole amount at the desk
        if kept > 0 and kept >= booking.total_amount:
            return PaymentStatus.PAID
        return PaymentStatus.PAY_AT_HOTEL
    if kept > 0:
        return PaymentStatus.PAID
    # Also a confirmed FULL booking with no money recorded: nothing was received yet
    return PaymentStatus.AWAITING
