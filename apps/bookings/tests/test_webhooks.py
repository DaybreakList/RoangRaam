import hashlib
import hmac
import json
import time
from decimal import Decimal
from smtplib import SMTPException
from unittest import mock

import pytest
import stripe
from django.core import mail
from django.urls import reverse

from apps.bookings.models import Booking, BookingRoom, Payment
from apps.bookings.payments import from_satang

from .factories import BookingFactory, BookingRoomFactory

pytestmark = pytest.mark.django_db

SECRET = 'whsec_test'


@pytest.fixture(autouse=True)
def webhook_secret(settings):
    settings.STRIPE_WEBHOOK_SECRET = SECRET


@pytest.fixture
def stripe_refund():
    with mock.patch('apps.bookings.payments.stripe.Refund.create') as create:
        create.return_value = mock.Mock(id='re_test_1')
        yield create


@pytest.fixture
def pending_booking(hotel_a):
    booking = BookingFactory(
        property=hotel_a, status=Booking.Status.PENDING_PAYMENT, total_amount=Decimal('3600.00'),
    )
    BookingRoomFactory(booking=booking)
    return booking


def _event(booking, event_type='checkout.session.completed', event_id='evt_1', property=None,
           payment_intent='pi_1', payment_status='paid'):
    return {
        'id': event_id,
        'type': event_type,
        'data': {'object': {
            'id': 'cs_test_1',
            'payment_status': payment_status,
            'payment_intent': payment_intent,
            'amount_total': 360000,
            'metadata': {
                'property_id': str((property or booking.property).pk),
                'booking_id': str(booking.pk),
            },
        }},
    }


def _post(client, event, secret=SECRET):
    payload = json.dumps(event)
    timestamp = int(time.time())
    signature = hmac.new(secret.encode(), f'{timestamp}.{payload}'.encode(), hashlib.sha256).hexdigest()
    return client.post(
        reverse('bookings:stripe-webhook'), payload, content_type='application/json',
        HTTP_STRIPE_SIGNATURE=f't={timestamp},v1={signature}',
    )


def _payments(booking):
    return list(
        Payment.scoped.for_property(booking.property).filter(booking=booking).order_by('created_at')
        .values_list('kind', 'amount', 'provider_ref'),
    )


def test_from_satang():
    assert from_satang(360000) == Decimal('3600.00')
    assert from_satang(150050) == Decimal('1500.50')


def test_paid_session_confirms_booking_and_sends_email(
    client, pending_booking, django_capture_on_commit_callbacks,
):
    with django_capture_on_commit_callbacks(execute=True):
        response = _post(client, _event(pending_booking))

    assert response.status_code == 200
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.CONFIRMED
    assert _payments(pending_booking) == [('CHARGE', Decimal('3600.00'), 'pi_1')]
    assert len(mail.outbox) == 1
    assert pending_booking.code in mail.outbox[0].subject


def test_repeated_event_is_recorded_once(client, pending_booking, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        _post(client, _event(pending_booking))
        response = _post(client, _event(pending_booking))

    assert response.status_code == 200
    assert len(_payments(pending_booking)) == 1
    assert len(mail.outbox) == 1


def test_bad_signature_is_rejected(client, pending_booking):
    response = _post(client, _event(pending_booking), secret='whsec_wrong')

    assert response.status_code == 400
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.PENDING_PAYMENT
    assert _payments(pending_booking) == []


def test_missing_signature_is_rejected(client, pending_booking):
    response = client.post(
        reverse('bookings:stripe-webhook'), json.dumps(_event(pending_booking)),
        content_type='application/json',
    )

    assert response.status_code == 400


def test_unpaid_completed_session_waits(client, pending_booking):
    # PromptPay: the guest has the QR code but has not paid yet
    response = _post(client, _event(pending_booking, payment_status='unpaid'))

    assert response.status_code == 200
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.PENDING_PAYMENT
    assert _payments(pending_booking) == []


def test_async_payment_succeeded_confirms_booking(client, pending_booking):
    _post(client, _event(pending_booking, event_type='checkout.session.async_payment_succeeded'))

    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.CONFIRMED


def test_payment_after_cancellation_is_refunded(client, pending_booking, stripe_refund):
    pending_booking.status = Booking.Status.CANCELLED
    pending_booking.save(update_fields=['status'])

    response = _post(client, _event(pending_booking))

    assert response.status_code == 200
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.CANCELLED
    assert _payments(pending_booking) == [
        ('CHARGE', Decimal('3600.00'), 'pi_1'),
        ('REFUND', Decimal('3600.00'), 're_test_1'),
    ]
    stripe_refund.assert_called_once()
    assert stripe_refund.call_args.kwargs['payment_intent'] == 'pi_1'
    assert stripe_refund.call_args.kwargs['idempotency_key'] == 'refund-pi_1'
    assert len(mail.outbox) == 0


def test_second_payment_for_same_booking_is_refunded(client, pending_booking, stripe_refund):
    _post(client, _event(pending_booking))
    _post(client, _event(pending_booking, event_id='evt_2', payment_intent='pi_2'))

    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.CONFIRMED
    assert [row[0] for row in _payments(pending_booking)] == ['CHARGE', 'CHARGE', 'REFUND']
    assert stripe_refund.call_args.kwargs['payment_intent'] == 'pi_2'


def test_refund_is_retried_when_stripe_failed(client, pending_booking, stripe_refund):
    pending_booking.status = Booking.Status.CANCELLED
    pending_booking.save(update_fields=['status'])
    stripe_refund.side_effect = [stripe.APIConnectionError('down'), mock.Mock(id='re_test_1')]

    with pytest.raises(stripe.APIConnectionError):
        _post(client, _event(pending_booking))
    # Stripe resends the event after our 500
    _post(client, _event(pending_booking))

    assert [row[0] for row in _payments(pending_booking)] == ['CHARGE', 'REFUND']


def test_expired_session_releases_unpaid_booking(client, pending_booking):
    response = _post(client, _event(pending_booking, event_type='checkout.session.expired'))

    assert response.status_code == 200
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.CANCELLED
    rooms = BookingRoom.scoped.for_property(pending_booking.property).filter(booking=pending_booking)
    assert not rooms.filter(is_active=True).exists()


def test_expired_session_keeps_paid_booking(client, hotel_a):
    # The guest opened two Checkout pages and paid with the other one
    booking = BookingFactory(property=hotel_a, status=Booking.Status.CONFIRMED)

    _post(client, _event(booking, event_type='checkout.session.expired'))

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED


def test_email_failure_does_not_undo_payment(client, pending_booking, django_capture_on_commit_callbacks):
    with mock.patch('apps.bookings.emails.EmailMultiAlternatives.send', side_effect=SMTPException):
        with django_capture_on_commit_callbacks(execute=True):
            response = _post(client, _event(pending_booking))

    assert response.status_code == 200
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.CONFIRMED


def test_booking_of_other_property_is_ignored(client, hotel_b, pending_booking, stripe_refund):
    # Metadata naming hotel B with a booking id of hotel A must not touch the booking
    response = _post(client, _event(pending_booking, property=hotel_b))

    assert response.status_code == 200
    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.PENDING_PAYMENT
    assert Payment.scoped.for_property(hotel_b).count() == 0
    assert _payments(pending_booking) == []


def test_webhook_rejects_get(client):
    assert client.get(reverse('bookings:stripe-webhook')).status_code == 405
