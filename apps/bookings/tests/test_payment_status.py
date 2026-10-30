from datetime import timedelta
from decimal import Decimal

import factory
import pytest
from django.utils import timezone

from apps.bookings.models import Booking, Payment
from apps.bookings.payment_status import PaymentStatus, payment_statuses
from apps.bookings.payments import PAYMENT_WINDOW, release_expired_bookings

from .factories import BookingFactory

Status = Booking.Status
Option = Booking.PaymentOption


class PaymentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Payment

    booking = factory.SubFactory(BookingFactory)
    property = factory.SelfAttribute('booking.property')
    kind = Payment.Kind.CHARGE
    amount = Decimal('3000.00')
    provider_ref = factory.Sequence(lambda n: f'pi_test_{n}')
    idempotency_key = factory.Sequence(lambda n: f'evt_test_{n}')


def _status_of(booking):
    return payment_statuses(booking.property, [booking])[booking.pk]


def test_labels_are_thai():
    assert dict(PaymentStatus.choices) == {
        'PAID': 'จ่ายแล้ว',
        'AWAITING': 'รอชำระ',
        'PAY_AT_HOTEL': 'จ่ายที่โรงแรม',
        'REFUND_PENDING': 'รอคืนเงิน',
        'REFUNDED': 'คืนเงินแล้ว',
        'NONE': 'ไม่มีการชำระ',
    }


@pytest.mark.parametrize('status', [Status.CONFIRMED, Status.CHECKED_IN, Status.CHECKED_OUT])
def test_full_booking_with_a_charge_is_paid(hotel_a, status):
    booking = BookingFactory(property=hotel_a, status=status)
    PaymentFactory(booking=booking)

    assert _status_of(booking) == PaymentStatus.PAID


def test_full_booking_waiting_for_payment_is_awaiting(hotel_a):
    booking = BookingFactory(property=hotel_a, status=Status.PENDING_PAYMENT)

    assert _status_of(booking) == PaymentStatus.AWAITING


def test_confirmed_full_booking_without_money_is_awaiting(hotel_a):
    # e.g. created through the admin: nothing was actually received
    booking = BookingFactory(property=hotel_a, status=Status.CONFIRMED)

    assert _status_of(booking) == PaymentStatus.AWAITING


@pytest.mark.parametrize('status', [Status.CONFIRMED, Status.CHECKED_IN, Status.CHECKED_OUT])
def test_pay_at_hotel_booking_is_pay_at_hotel(hotel_a, status):
    booking = BookingFactory(property=hotel_a, status=status, payment_option=Option.PAY_AT_HOTEL)

    assert _status_of(booking) == PaymentStatus.PAY_AT_HOTEL


def test_cancelled_booking_with_its_charge_refunded_is_refunded(hotel_a):
    booking = BookingFactory(property=hotel_a, status=Status.CANCELLED)
    PaymentFactory(booking=booking)
    PaymentFactory(booking=booking, kind=Payment.Kind.REFUND, provider_ref='re_test_1')

    assert _status_of(booking) == PaymentStatus.REFUNDED


@pytest.mark.parametrize('option', [Option.FULL, Option.PAY_AT_HOTEL])
def test_cancelled_booking_never_paid_has_no_payment(hotel_a, option):
    booking = BookingFactory(property=hotel_a, status=Status.CANCELLED, payment_option=option)

    assert _status_of(booking) == PaymentStatus.NONE


def test_cancelled_booking_with_a_charge_not_yet_refunded_is_refund_pending(hotel_a):
    # A payment that arrived after the cancel, whose automatic refund failed
    booking = BookingFactory(property=hotel_a, status=Status.CANCELLED)
    PaymentFactory(booking=booking)

    assert _status_of(booking) == PaymentStatus.REFUND_PENDING


def test_cancelled_booking_with_one_of_two_charges_refunded_is_refund_pending(hotel_a):
    booking = BookingFactory(property=hotel_a, status=Status.CANCELLED)
    PaymentFactory(booking=booking)
    PaymentFactory(booking=booking)
    PaymentFactory(booking=booking, kind=Payment.Kind.REFUND)

    assert _status_of(booking) == PaymentStatus.REFUND_PENDING


def test_booking_paid_twice_with_the_extra_charge_refunded_is_paid(hotel_a):
    # Two Checkout pages paid: the webhook keeps the first charge and refunds the second
    booking = BookingFactory(property=hotel_a, status=Status.CONFIRMED)
    PaymentFactory(booking=booking)
    PaymentFactory(booking=booking)
    PaymentFactory(booking=booking, kind=Payment.Kind.REFUND)

    assert _status_of(booking) == PaymentStatus.PAID


def test_booking_released_for_not_paying_in_time_has_no_payment(hotel_a):
    booking = BookingFactory(property=hotel_a, status=Status.PENDING_PAYMENT)
    Booking.scoped.for_property(hotel_a).filter(pk=booking.pk).update(
        created_at=timezone.now() - PAYMENT_WINDOW - timedelta(minutes=1),
    )

    assert release_expired_bookings(hotel_a) == 1
    booking.refresh_from_db()
    assert booking.status == Status.CANCELLED
    assert _status_of(booking) == PaymentStatus.NONE


def test_empty_list_returns_empty_dict_without_a_query(hotel_a, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert payment_statuses(hotel_a, []) == {}


def test_accepts_any_iterable_of_bookings(hotel_a):
    booking = BookingFactory(property=hotel_a, status=Status.PENDING_PAYMENT)

    assert payment_statuses(hotel_a, iter([booking])) == {booking.pk: PaymentStatus.AWAITING}


def test_booking_of_another_property_raises(hotel_a, hotel_b):
    own = BookingFactory(property=hotel_a)
    other = BookingFactory(property=hotel_b)

    with pytest.raises(ValueError):
        payment_statuses(hotel_a, [own, other])


def test_payment_rows_of_another_property_are_not_counted(hotel_a, hotel_b):
    booking = BookingFactory(property=hotel_a, status=Status.CONFIRMED)
    # A row scoped to hotel B that points at hotel A's booking must not make it paid
    PaymentFactory(booking=booking, property=hotel_b)

    assert _status_of(booking) == PaymentStatus.AWAITING


def test_each_property_sees_only_its_own_money(hotel_a, hotel_b):
    booking_a = BookingFactory(property=hotel_a, status=Status.CANCELLED)
    PaymentFactory(booking=booking_a)
    PaymentFactory(booking=booking_a, kind=Payment.Kind.REFUND)
    booking_b = BookingFactory(property=hotel_b, status=Status.CANCELLED)
    PaymentFactory(booking=booking_b)

    assert payment_statuses(hotel_a, [booking_a]) == {booking_a.pk: PaymentStatus.REFUNDED}
    assert payment_statuses(hotel_b, [booking_b]) == {booking_b.pk: PaymentStatus.REFUND_PENDING}


@pytest.mark.parametrize('count', [1, 10])
def test_runs_one_query_however_many_bookings(hotel_a, django_assert_num_queries, count):
    bookings = []
    for index in range(count):
        booking = BookingFactory(property=hotel_a, status=Status.CONFIRMED)
        if index % 2:
            PaymentFactory(booking=booking)
        bookings.append(booking)

    with django_assert_num_queries(1):
        statuses = payment_statuses(hotel_a, bookings)

    assert len(statuses) == count
