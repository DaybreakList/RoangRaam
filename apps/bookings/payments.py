from datetime import timedelta
from decimal import Decimal

import stripe
from django.conf import settings
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404
from django.utils import timezone

from .cancellation import cancel_booking
from .models import Booking

# A booking waiting for payment holds its rooms for this long
PAYMENT_WINDOW = timedelta(minutes=30)
# Stripe rejects a Checkout Session that lives under 30 minutes; the extra
# minute covers clock drift between us and Stripe
CHECKOUT_LIFETIME = timedelta(minutes=31)
CURRENCY = 'thb'


def to_satang(amount):
    """Stripe takes the smallest unit as an int: Decimal('1500.50') -> 150050."""
    return int((amount * 100).quantize(Decimal('1')))


def start_checkout(property, booking_id, success_url, cancel_url):
    """
    Open a Stripe Checkout page for a booking of `property` and return its URL.

    Raises Http404 for another property's booking. Raises ValidationError keyed
    by 'status' when the booking is not waiting for payment, and cancels the
    booking first when its payment window has already passed.
    """
    booking = get_object_or_404(
        Booking.scoped.for_property(property).select_related('guest'), pk=booking_id,
    )
    if booking.status != Booking.Status.PENDING_PAYMENT:
        raise ValidationError({'status': 'การจองนี้ไม่ได้อยู่ในสถานะรอชำระเงิน'})
    if timezone.now() >= booking.created_at + PAYMENT_WINDOW:
        cancel_booking(property, booking.pk)
        raise ValidationError({'status': 'หมดเวลาชำระเงิน การจองนี้ถูกยกเลิกแล้ว กรุณาจองใหม่'})

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
