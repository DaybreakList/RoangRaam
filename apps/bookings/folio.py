"""
Folio and bill closing at the front desk (TASK-48).

Rules agreed by the team for Sprint 3:
- Staff take one payment at the desk for the whole balance. The amount is
  computed here from the booking, never sent by the browser. Cash and card are
  not told apart.
- Only a CHECKED_IN booking takes desk payments and closes its bill, and only
  once nothing is left to pay. check_out() refuses a booking whose bill is open.
- Closing the bill issues a Receipt with the next number of that property and
  copies the total and tax rate, so later changes to the property do not change it.
- Room prices already include VAT (API_CONTRACT); the folio only splits it out.
"""

import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.shortcuts import get_object_or_404

from apps.properties.models import Property

from .models import Booking, BookingNightRate, Payment, Receipt

CENT = Decimal('0.01')
NOT_CHECKED_IN_MESSAGE = 'รับชำระและปิดบิลได้เฉพาะการจองที่เช็คอินแล้ว'
BILL_CLOSED_MESSAGE = 'ปิดบิลการจองนี้แล้ว'
NOTHING_DUE_MESSAGE = 'การจองนี้ไม่มียอดค้างชำระ'
BALANCE_DUE_MESSAGE = 'ยังมียอดค้างชำระ กรุณารับชำระให้ครบก่อนปิดบิล'
BAD_KEY_MESSAGE = 'คำขอไม่ถูกต้อง กรุณาโหลดหน้าใหม่แล้วลองอีกครั้ง'


@dataclass(frozen=True)
class FolioLine:
    room_number: str
    room_type_name: str
    night: object  # datetime.date
    price: Decimal


@dataclass(frozen=True)
class Folio:
    booking: Booking
    lines: list
    total: Decimal
    tax_rate: Decimal
    tax_amount: Decimal
    net_amount: Decimal
    payments: list
    paid: Decimal
    balance: Decimal
    receipt: Receipt | None

    @property
    def is_closed(self):
        return self.receipt is not None


def vat_included(total, tax_rate):
    """VAT inside a price that already includes it: 1070.00 at 7% -> 70.00."""
    if not tax_rate:
        return Decimal('0.00')
    return (total * tax_rate / (Decimal('100') + tax_rate)).quantize(CENT, rounding=ROUND_HALF_UP)


def get_folio(property, booking):
    """
    Everything the guest is charged and has paid for `booking` of `property`.

    Before the bill is closed, VAT uses the property's current tax rate; after,
    it is read from the Receipt. Runs 3 queries for any number of rooms and nights.
    Raises ValueError when the booking belongs to another property, since that
    means the caller's own query leaked across hotels.
    """
    if booking.property_id != property.pk:
        raise ValueError(f'Booking {booking.pk} does not belong to property {property.pk}')

    night_rates = (
        BookingNightRate.scoped.for_property(property)
        .filter(booking_room__booking=booking, booking_room__is_active=True)
        .select_related('booking_room__room', 'booking_room__room_type')
        .order_by('night', 'booking_room__room__number', 'pk')
    )
    lines = [
        FolioLine(
            room_number=rate.booking_room.room.number,
            room_type_name=rate.booking_room.room_type.name,
            night=rate.night,
            price=rate.price,
        )
        for rate in night_rates
    ]
    payments = list(
        Payment.scoped.for_property(property).filter(booking=booking).order_by('created_at', 'pk')
    )
    receipt = Receipt.scoped.for_property(property).filter(booking=booking).first()

    # The amount the guest agreed to pay; the night lines add up to it
    total = booking.total_amount
    tax_rate = receipt.tax_rate if receipt else property.tax_rate
    tax_amount = receipt.tax_amount if receipt else vat_included(total, tax_rate)
    paid = _paid(payments)
    return Folio(
        booking=booking,
        lines=lines,
        total=total,
        tax_rate=tax_rate,
        tax_amount=tax_amount,
        net_amount=total - tax_amount,
        payments=payments,
        paid=paid,
        balance=total - paid,
        receipt=receipt,
    )


def record_desk_payment(property, booking_id, *, user, idempotency_key):
    """
    Record the guest paying the whole balance of a CHECKED_IN booking at the desk.

    `idempotency_key` is a UUID the folio form sends with each submit; sending
    the same key again returns the first Payment instead of adding one. Raises
    Http404 for another property's booking, and ValidationError keyed by
    'status' or 'balance' when the rules above are broken.
    """
    key = _desk_key(idempotency_key)
    with transaction.atomic():
        booking = _lock_booking(property, booking_id)
        payments = Payment.scoped.for_property(property)
        existing = payments.filter(idempotency_key=key).first()
        if existing is not None:
            if existing.booking_id != booking.pk:
                # The browser reused a key from another booking's form
                raise ValidationError({'status': BAD_KEY_MESSAGE})
            return existing
        _check_open(property, booking)

        balance = booking.total_amount - _paid(payments.filter(booking=booking))
        if balance <= 0:
            raise ValidationError({'balance': NOTHING_DUE_MESSAGE})
        return payments.create(
            property=property,
            booking=booking,
            kind=Payment.Kind.CHARGE,
            source=Payment.Source.FRONT_DESK,
            amount=balance,
            provider_ref=key,
            idempotency_key=key,
            recorded_by=user,
        )


def close_bill(property, booking_id, *, user):
    """
    Close the bill of a CHECKED_IN booking with nothing left to pay and issue its Receipt.

    Closing twice returns the Receipt issued the first time. Raises Http404 for
    another property's booking, and ValidationError keyed by 'status' or
    'balance' when the rules above are broken.
    """
    with transaction.atomic():
        # Lock the property first: receipts of one property are numbered one at a time
        Property.objects.select_for_update().get(pk=property.pk)
        booking = _lock_booking(property, booking_id)
        receipts = Receipt.scoped.for_property(property)
        existing = receipts.filter(booking=booking).first()
        if existing is not None:
            return existing
        if booking.status != Booking.Status.CHECKED_IN:
            raise ValidationError({'status': NOT_CHECKED_IN_MESSAGE})

        paid = _paid(Payment.scoped.for_property(property).filter(booking=booking))
        if paid != booking.total_amount:
            raise ValidationError({'balance': BALANCE_DUE_MESSAGE})

        last = receipts.aggregate(last=Max('number'))['last'] or 0
        # Read again inside the lock, in case the owner changed it a moment ago
        tax_rate = Property.objects.values_list('tax_rate', flat=True).get(pk=property.pk)
        return receipts.create(
            property=property,
            booking=booking,
            number=last + 1,
            total=booking.total_amount,
            tax_rate=tax_rate,
            tax_amount=vat_included(booking.total_amount, tax_rate),
            issued_by=user,
        )


def bill_is_closed(property, booking):
    """Whether `booking` of `property` has a Receipt. Used by check_out()."""
    return Receipt.scoped.for_property(property).filter(booking=booking).exists()


def _lock_booking(property, booking_id):
    return get_object_or_404(
        Booking.scoped.for_property(property).select_for_update(), pk=booking_id,
    )


def _check_open(property, booking):
    if booking.status != Booking.Status.CHECKED_IN:
        raise ValidationError({'status': NOT_CHECKED_IN_MESSAGE})
    if bill_is_closed(property, booking):
        raise ValidationError({'status': BILL_CLOSED_MESSAGE})


def _desk_key(idempotency_key):
    try:
        return f'desk-{uuid.UUID(str(idempotency_key))}'
    except ValueError:
        raise ValidationError({'status': BAD_KEY_MESSAGE}) from None


def _paid(payments):
    """CHARGE total minus REFUND total of `payments` (rows or a queryset)."""
    paid = Decimal('0.00')
    for payment in payments:
        paid += payment.amount if payment.kind == Payment.Kind.CHARGE else -payment.amount
    return paid
