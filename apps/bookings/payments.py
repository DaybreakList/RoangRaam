import logging
from datetime import timedelta
from decimal import Decimal
from functools import partial

import stripe
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.properties.models import Property

from .cancellation import cancel_booking
from .emails import send_booking_confirmation_safely
from .models import Booking, BookingRoom, Payment

logger = logging.getLogger(__name__)

# Stripe rejects a Checkout Session that lives under 30 minutes; the extra
# minute covers clock drift between us and Stripe
CHECKOUT_LIFETIME = timedelta(minutes=31)
# A guest can open a Checkout page only this long after booking
CHECKOUT_DEADLINE = timedelta(minutes=25)
# Time for Stripe to deliver the webhook of a payment made at the last moment
WEBHOOK_GRACE = timedelta(minutes=4)
# A booking waiting for payment holds its rooms for this long (60 minutes). It
# outlasts the last Checkout page a guest can open, so a guest who pays always
# keeps the booking instead of being refunded for a released room.
PAYMENT_WINDOW = CHECKOUT_DEADLINE + CHECKOUT_LIFETIME + WEBHOOK_GRACE
CURRENCY = 'thb'
NOT_PENDING_MESSAGE = 'การจองนี้ไม่ได้อยู่ในสถานะรอชำระเงิน'
CHECKOUT_CLOSED_MESSAGE = 'หมดเวลาชำระเงิน กรุณาทำการจองใหม่'
REFUND_FAILED_MESSAGE = 'คืนเงินไม่สำเร็จ กรุณาลองใหม่อีกครั้ง'


def to_satang(amount):
    """Stripe takes the smallest unit as an int: Decimal('1500.50') -> 150050."""
    return int((amount * 100).quantize(Decimal('1')))


def from_satang(amount):
    """Back from Stripe's int to baht: 150050 -> Decimal('1500.50')."""
    return (Decimal(amount) / 100).quantize(Decimal('0.01'))


def checkout_is_open(booking):
    """Whether the guest may still open a Checkout page for `booking`."""
    return (
        booking.status == Booking.Status.PENDING_PAYMENT
        and timezone.now() < booking.created_at + CHECKOUT_DEADLINE
    )


def start_checkout(property, booking_id, success_url, cancel_url):
    """
    Open a Stripe Checkout page for a booking of `property` and return its URL.

    Raises Http404 for another property's booking. Raises ValidationError keyed
    by 'status' when the booking is not waiting for payment, or when
    CHECKOUT_DEADLINE has passed. The booking is not cancelled here: a Checkout
    page opened earlier may still be paid, and release_expired_bookings() cancels
    it once PAYMENT_WINDOW has passed.
    """
    booking = get_object_or_404(
        Booking.scoped.for_property(property).select_related('guest'), pk=booking_id,
    )
    if booking.status != Booking.Status.PENDING_PAYMENT:
        raise ValidationError({'status': NOT_PENDING_MESSAGE})
    if not checkout_is_open(booking):
        raise ValidationError({'status': CHECKOUT_CLOSED_MESSAGE})

    # The webhook reads these back to find the booking without trusting the browser
    metadata = {'property_id': str(property.pk), 'booking_id': str(booking.pk)}
    session = stripe.checkout.Session.create(
        api_key=settings.STRIPE_SECRET_KEY,
        mode='payment',
        line_items=[{
            'quantity': 1,
            'price_data': {
                'currency': CURRENCY,
                'unit_amount': to_satang(booking.total_amount),
                'product_data': {'name': f'{property.name} การจอง {booking.code}'},
            },
        }],
        customer_email=booking.guest.email,
        client_reference_id=str(booking.pk),
        metadata=metadata,
        payment_intent_data={'metadata': metadata},
        expires_at=int((timezone.now() + CHECKOUT_LIFETIME).timestamp()),
        success_url=success_url,
        cancel_url=cancel_url,
    )
    return session.url


def _locked_booking(session):
    """
    The booking a Checkout Session was opened for, locked with select_for_update().

    Must run inside transaction.atomic(). Returns (None, None) when the metadata
    does not point at a booking of that property. The ids come from our own
    start_checkout() through a signed webhook, never from the browser.
    """
    metadata = session.get('metadata') or {}
    try:
        property = Property.objects.filter(pk=metadata.get('property_id')).first()
        booking = None
        if property is not None:
            booking = (
                Booking.scoped.for_property(property)
                .select_for_update()
                .filter(pk=metadata.get('booking_id'))
                .first()
            )
    except ValidationError:  # not a UUID
        property = booking = None
    if booking is None:
        logger.warning('Stripe session %s has no matching booking', session.get('id'))
        return None, None
    return property, booking


def record_checkout_payment(event_id, session):
    """
    Save the money of a paid Checkout Session and confirm its booking.

    Safe to run again for the same payment (Stripe retries webhooks): the
    PaymentIntent id is unique per property, so a repeat finds the CHARGE row
    instead of adding one. Money that cannot be kept, because the booking was
    cancelled meanwhile (payment window passed) or was already paid through
    another Checkout page, is recorded as a CHARGE and then refunded in full;
    the booking status is left as it is.
    """
    with transaction.atomic():
        property, booking = _locked_booking(session)
        if booking is None:
            return None
        payments = Payment.scoped.for_property(property)
        charge = payments.filter(provider_ref=session['payment_intent']).first()
        if charge is None:
            charge = payments.create(
                property=property,
                booking=booking,
                kind=Payment.Kind.CHARGE,
                amount=from_satang(session['amount_total']),
                provider_ref=session['payment_intent'],
                idempotency_key=event_id,
            )
            if booking.status == Booking.Status.PENDING_PAYMENT:
                booking.status = Booking.Status.CONFIRMED
                booking.save(update_fields=['status'])
                # After commit, so a slow or failing mail server cannot undo the payment
                transaction.on_commit(partial(send_booking_confirmation_safely, property, booking.pk))

        first_charge = payments.filter(booking=booking, kind=Payment.Kind.CHARGE).earliest('created_at')
        keep = booking.status != Booking.Status.CANCELLED and first_charge.pk == charge.pk
    if not keep:
        refund_charge(property, charge)
    return charge


def refund_charge(property, charge):
    """
    Give a CHARGE back in full through Stripe and record a REFUND row.

    Safe to repeat: the same idempotency key goes to Stripe, which then returns
    the first refund instead of making a second one, and to our REFUND row.
    """
    key = f'refund-{charge.provider_ref}'
    payments = Payment.scoped.for_property(property)
    if payments.filter(idempotency_key=key).exists():
        return None
    refund = stripe.Refund.create(
        api_key=settings.STRIPE_SECRET_KEY,
        payment_intent=charge.provider_ref,
        idempotency_key=key,
    )
    try:
        with transaction.atomic():
            return payments.create(
                property=property,
                booking_id=charge.booking_id,
                kind=Payment.Kind.REFUND,
                amount=charge.amount,
                provider_ref=refund.id,
                idempotency_key=key,
            )
    except IntegrityError:
        # A parallel delivery of the same webhook recorded it first
        return None


def refund_booking(property, booking):
    """
    Refund every CHARGE of `booking` that has not been refunded yet.

    Used by cancel_booking() while it holds the booking lock. A Stripe error
    becomes a ValidationError keyed by 'status'; refund_charge() sends the same
    idempotency key on the retry, so the guest is never refunded twice.
    """
    charges = Payment.scoped.for_property(property).filter(booking=booking, kind=Payment.Kind.CHARGE)
    try:
        for charge in charges.order_by('created_at'):
            refund_charge(property, charge)
    except stripe.StripeError as error:
        logger.exception('Refund for booking %s failed', booking.pk)
        raise ValidationError({'status': REFUND_FAILED_MESSAGE}) from error


def release_unpaid_booking(session):
    """Cancel the booking of an expired or failed Checkout page if it is still unpaid."""
    with transaction.atomic():
        property, booking = _locked_booking(session)
        # Another Checkout page may have paid it already; only an unpaid booking goes
        if booking is not None and booking.status == Booking.Status.PENDING_PAYMENT:
            cancel_booking(property, booking.pk)


def release_expired_bookings(property, room_types=None):
    """
    Cancel bookings of `property` still unpaid PAYMENT_WINDOW after they were made.

    Covers guests who never opened the payment page, so Stripe never sends an
    expired event. `room_types` (instances or ids) limits it to bookings holding
    those types, for a caller that is about to book them. Each booking is locked
    and re-checked on its own; one that another request has locked is skipped
    rather than waited for, since that request is paying or cancelling it.
    Returns how many bookings were cancelled.
    """
    expired = Booking.scoped.for_property(property).filter(
        status=Booking.Status.PENDING_PAYMENT,
        created_at__lte=timezone.now() - PAYMENT_WINDOW,
    )
    if room_types is not None:
        holding = BookingRoom.scoped.for_property(property).filter(room_type__in=room_types)
        expired = expired.filter(pk__in=holding.values('booking_id'))

    released = 0
    for booking_id in list(expired.values_list('pk', flat=True)):
        with transaction.atomic():
            booking = expired.select_for_update(skip_locked=True).filter(pk=booking_id).first()
            if booking is not None:
                cancel_booking(property, booking.pk)
                released += 1
    return released
