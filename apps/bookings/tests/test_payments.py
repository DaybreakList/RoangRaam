from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
import stripe
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.urls import reverse
from django.utils import timezone

from apps.bookings.models import Booking, Payment
from apps.bookings.payments import start_checkout, to_satang

from .factories import BookingFactory

pytestmark = pytest.mark.django_db

SUCCESS_URL = 'http://testserver/done/'
CANCEL_URL = 'http://testserver/cancel/'


@pytest.fixture
def stripe_create():
    with mock.patch('apps.bookings.payments.stripe.checkout.Session.create') as create:
        create.return_value = mock.Mock(url='https://checkout.stripe.com/c/pay/cs_test_1')
        yield create


@pytest.fixture
def pending_booking(hotel_a):
    return BookingFactory(
        property=hotel_a, status=Booking.Status.PENDING_PAYMENT, total_amount=Decimal('3500.50'),
    )


def test_to_satang():
    assert to_satang(Decimal('3500.50')) == 350050
    assert to_satang(Decimal('0.01')) == 1


def test_start_checkout_returns_stripe_url(hotel_a, pending_booking, stripe_create):
    url = start_checkout(hotel_a, pending_booking.pk, SUCCESS_URL, CANCEL_URL)

    assert url == 'https://checkout.stripe.com/c/pay/cs_test_1'
    kwargs = stripe_create.call_args.kwargs
    assert kwargs['line_items'][0]['price_data']['unit_amount'] == 350050
    assert kwargs['line_items'][0]['price_data']['currency'] == 'thb'
    assert kwargs['metadata'] == {
        'property_id': str(hotel_a.pk), 'booking_id': str(pending_booking.pk),
    }
    assert kwargs['customer_email'] == pending_booking.guest.email
    assert kwargs['success_url'] == SUCCESS_URL


def test_start_checkout_rejects_booking_not_waiting_for_payment(hotel_a, stripe_create):
    booking = BookingFactory(property=hotel_a, status=Booking.Status.CONFIRMED)

    with pytest.raises(ValidationError) as exc:
        start_checkout(hotel_a, booking.pk, SUCCESS_URL, CANCEL_URL)

    assert 'status' in exc.value.message_dict
    stripe_create.assert_not_called()


def test_start_checkout_cancels_booking_past_payment_window(hotel_a, pending_booking, stripe_create):
    Booking.scoped.for_property(hotel_a).filter(pk=pending_booking.pk).update(
        created_at=timezone.now() - timedelta(minutes=31),
    )

    with pytest.raises(ValidationError):
        start_checkout(hotel_a, pending_booking.pk, SUCCESS_URL, CANCEL_URL)

    pending_booking.refresh_from_db()
    assert pending_booking.status == Booking.Status.CANCELLED
    stripe_create.assert_not_called()


def test_start_checkout_404_for_other_property_booking(hotel_b, pending_booking, stripe_create):
    with pytest.raises(Http404):
        start_checkout(hotel_b, pending_booking.pk, SUCCESS_URL, CANCEL_URL)

    stripe_create.assert_not_called()


def _charge(booking, **overrides):
    fields = {
        'property': booking.property, 'booking': booking, 'kind': Payment.Kind.CHARGE,
        'amount': Decimal('3500.50'), 'provider_ref': 'pi_test_1', 'idempotency_key': 'evt_test_1',
    }
    fields.update(overrides)
    return Payment.scoped.for_property(booking.property).create(**fields)


def test_payment_cannot_be_changed_or_deleted(pending_booking):
    payment = _charge(pending_booking)

    payment.amount = Decimal('1.00')
    with pytest.raises(ValidationError):
        payment.save()
    with pytest.raises(ValidationError):
        payment.delete()


def test_payment_idempotency_key_is_unique_per_property(pending_booking):
    _charge(pending_booking)

    with pytest.raises(IntegrityError):
        _charge(pending_booking, provider_ref='pi_test_2')


def test_payment_booking_must_be_same_property(hotel_b, pending_booking):
    payment = Payment(
        property=hotel_b, booking=pending_booking, kind=Payment.Kind.CHARGE,
        amount=Decimal('10.00'), provider_ref='pi_x', idempotency_key='evt_x',
    )

    with pytest.raises(ValidationError) as exc:
        payment.full_clean()

    assert 'booking' in exc.value.message_dict


def _pay_url(property, booking):
    return reverse('bookings:pay', kwargs={'property_slug': property.slug, 'booking_id': booking.pk})


def _result_url(property, booking):
    return reverse(
        'bookings:payment-result', kwargs={'property_slug': property.slug, 'booking_id': booking.pk},
    )


def test_pay_view_redirects_to_stripe(client, hotel_a, pending_booking, stripe_create):
    response = client.post(_pay_url(hotel_a, pending_booking))

    assert response.status_code == 302
    assert response['Location'] == 'https://checkout.stripe.com/c/pay/cs_test_1'
    result_url = 'http://testserver' + _result_url(hotel_a, pending_booking)
    assert stripe_create.call_args.kwargs['success_url'] == result_url
    assert stripe_create.call_args.kwargs['cancel_url'] == result_url


def test_pay_view_rejects_get(client, hotel_a, pending_booking, stripe_create):
    response = client.get(_pay_url(hotel_a, pending_booking))

    assert response.status_code == 405
    stripe_create.assert_not_called()


def test_pay_view_sends_paid_booking_to_result_page(client, hotel_a, stripe_create):
    booking = BookingFactory(property=hotel_a, status=Booking.Status.CONFIRMED)

    response = client.post(_pay_url(hotel_a, booking))

    assert response.status_code == 302
    assert response['Location'].endswith(_result_url(hotel_a, booking))
    stripe_create.assert_not_called()


def test_pay_view_shows_error_when_stripe_fails(client, hotel_a, pending_booking, stripe_create):
    stripe_create.side_effect = stripe.APIConnectionError('down')

    response = client.post(_pay_url(hotel_a, pending_booking))

    assert response.status_code == 502
    assert 'ระบบชำระเงินขัดข้องชั่วคราว' in response.content.decode()


def test_pay_view_other_property_booking_is_404(client, hotel_b, pending_booking, stripe_create):
    response = client.post(_pay_url(hotel_b, pending_booking))

    assert response.status_code == 404
    stripe_create.assert_not_called()


@pytest.mark.parametrize('status,text', [
    (Booking.Status.PENDING_PAYMENT, 'รอชำระเงิน'),
    (Booking.Status.CONFIRMED, 'ชำระเงินเรียบร้อย'),
    (Booking.Status.CANCELLED, 'หมดเวลาชำระเงิน'),
])
def test_payment_result_shows_booking_status(client, hotel_a, status, text):
    booking = BookingFactory(property=hotel_a, status=status)

    response = client.get(_result_url(hotel_a, booking))

    assert response.status_code == 200
    assert booking.code in response.content.decode()
    assert text in response.content.decode()


def test_payment_result_other_property_booking_is_404(client, hotel_b, pending_booking):
    response = client.get(_result_url(hotel_b, pending_booking))

    assert response.status_code == 404
