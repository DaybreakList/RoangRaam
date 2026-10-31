import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.http import Http404

from apps.bookings.folio import (
    BALANCE_DUE_MESSAGE,
    NOT_CHECKED_IN_MESSAGE,
    NOTHING_DUE_MESSAGE,
    bill_is_closed,
    close_bill,
    get_folio,
    record_desk_payment,
    vat_included,
)
from apps.bookings.models import Booking, Payment, Receipt
from apps.properties.models import Property
from apps.properties.tests.factories import UserFactory
from apps.rooms.tests.factories import RoomFactory

from .factories import BookingFactory, BookingNightRateFactory, BookingRoomFactory

Status = Booking.Status


def _booking(property, status=Status.CHECKED_IN, prices=('1070.00', '1070.00'), rooms=1, **kwargs):
    """A booking with `rooms` rooms, one night per price, and total_amount to match."""
    nights = len(prices)
    booking = BookingFactory(property=property, status=status, **kwargs)
    booking.check_out_date = booking.check_in_date + timedelta(days=nights)
    booking.total_amount = sum((Decimal(price) for price in prices), Decimal('0.00')) * rooms
    booking.save()
    for _ in range(rooms):
        booking_room = BookingRoomFactory(booking=booking, room=RoomFactory(property=property))
        for offset, price in enumerate(prices):
            BookingNightRateFactory(
                booking_room=booking_room,
                night=booking.check_in_date + timedelta(days=offset),
                price=Decimal(price),
            )
    return booking


def _stripe_charge(booking, amount=None, kind=Payment.Kind.CHARGE):
    ref = f'pi_{uuid.uuid4().hex}'
    return Payment.objects.create(
        property=booking.property, booking=booking, kind=kind,
        amount=amount if amount is not None else booking.total_amount,
        provider_ref=ref, idempotency_key=f'evt_{ref}',
    )


def _pay_at_desk(booking, user=None, key=None):
    return record_desk_payment(
        booking.property, booking.pk, user=user or UserFactory(), idempotency_key=key or uuid.uuid4(),
    )


@pytest.fixture
def staff(db):
    return UserFactory()


# --- vat_included -----------------------------------------------------------

@pytest.mark.parametrize('total, rate, expected', [
    ('1070.00', '7.00', '70.00'),
    ('1000.00', '7.00', '65.42'),  # 65.4205... rounds down
    ('0.00', '7.00', '0.00'),
    ('1000.00', '0.00', '0.00'),
])
def test_vat_included_splits_tax_out_of_a_price_that_includes_it(total, rate, expected):
    assert vat_included(Decimal(total), Decimal(rate)) == Decimal(expected)


# --- get_folio --------------------------------------------------------------

def test_folio_lists_every_night_of_every_room_and_splits_out_vat(hotel_a, django_assert_num_queries):
    booking = _booking(hotel_a, prices=('1070.00', '2140.00'), rooms=2)

    with django_assert_num_queries(3):
        folio = get_folio(hotel_a, booking)

    assert len(folio.lines) == 4
    assert [line.night for line in folio.lines] == sorted(line.night for line in folio.lines)
    assert sum(line.price for line in folio.lines) == folio.total == Decimal('6420.00')
    assert folio.tax_rate == Decimal('7.00')
    assert folio.tax_amount == Decimal('420.00')
    assert folio.net_amount == Decimal('6000.00')
    assert folio.paid == Decimal('0.00')
    assert folio.balance == Decimal('6420.00')
    assert folio.payments == []
    assert not folio.is_closed


def test_folio_counts_refunds_against_charges(hotel_a):
    booking = _booking(hotel_a)
    _stripe_charge(booking)
    _stripe_charge(booking, amount=Decimal('500.00'), kind=Payment.Kind.REFUND)

    folio = get_folio(hotel_a, booking)

    assert folio.paid == Decimal('1640.00')
    assert folio.balance == Decimal('500.00')
    assert len(folio.payments) == 2


def test_folio_leaves_out_rooms_no_longer_active(hotel_a):
    booking = _booking(hotel_a, rooms=2)
    booking.booking_rooms.filter(pk=booking.booking_rooms.first().pk).update(is_active=False)

    assert len(get_folio(hotel_a, booking).lines) == 2


def test_folio_refuses_a_booking_of_another_property(hotel_a, hotel_b):
    booking = _booking(hotel_b)

    with pytest.raises(ValueError):
        get_folio(hotel_a, booking)


# --- record_desk_payment ----------------------------------------------------

def test_desk_payment_takes_the_whole_balance(hotel_a, staff):
    booking = _booking(hotel_a, payment_option=Booking.PaymentOption.PAY_AT_HOTEL)

    payment = _pay_at_desk(booking, user=staff)

    assert payment.kind == Payment.Kind.CHARGE
    assert payment.source == Payment.Source.FRONT_DESK
    assert payment.amount == Decimal('2140.00')
    assert payment.recorded_by == staff
    assert get_folio(hotel_a, booking).balance == Decimal('0.00')


def test_desk_payment_takes_only_what_is_left_after_stripe(hotel_a):
    booking = _booking(hotel_a)
    _stripe_charge(booking, amount=Decimal('1070.00'))

    assert _pay_at_desk(booking).amount == Decimal('1070.00')


def test_desk_payment_sent_twice_is_recorded_once(hotel_a):
    booking = _booking(hotel_a)
    key = uuid.uuid4()

    first = _pay_at_desk(booking, key=key)
    second = _pay_at_desk(booking, key=key)

    assert first.pk == second.pk
    assert Payment.objects.filter(booking=booking).count() == 1


def test_desk_payment_key_from_another_booking_is_refused(hotel_a):
    key = uuid.uuid4()
    _pay_at_desk(_booking(hotel_a), key=key)
    other = _booking(hotel_a)

    with pytest.raises(ValidationError):
        _pay_at_desk(other, key=key)
    assert not Payment.objects.filter(booking=other).exists()


def test_desk_payment_with_a_key_that_is_not_a_uuid_is_refused(hotel_a):
    booking = _booking(hotel_a)

    with pytest.raises(ValidationError):
        record_desk_payment(hotel_a, booking.pk, user=None, idempotency_key='not-a-uuid')
    assert not Payment.objects.exists()


@pytest.mark.parametrize('status', [
    Status.PENDING_PAYMENT, Status.CONFIRMED, Status.CHECKED_OUT, Status.CANCELLED,
])
def test_desk_payment_needs_a_checked_in_booking(hotel_a, status):
    booking = _booking(hotel_a, status=status)

    with pytest.raises(ValidationError) as error:
        _pay_at_desk(booking)
    assert error.value.message_dict == {'status': [NOT_CHECKED_IN_MESSAGE]}
    assert not Payment.objects.exists()


def test_desk_payment_with_nothing_due_is_refused(hotel_a):
    booking = _booking(hotel_a)
    _stripe_charge(booking)

    with pytest.raises(ValidationError) as error:
        _pay_at_desk(booking)
    assert error.value.message_dict == {'balance': [NOTHING_DUE_MESSAGE]}
    assert Payment.objects.filter(booking=booking).count() == 1


def test_desk_payment_after_the_bill_is_closed_is_refused(hotel_a, staff):
    booking = _booking(hotel_a)
    _stripe_charge(booking)
    close_bill(hotel_a, booking.pk, user=staff)

    with pytest.raises(ValidationError):
        _pay_at_desk(booking)


def test_desk_payment_for_a_booking_of_another_property_is_404(hotel_a, hotel_b):
    booking = _booking(hotel_b)

    with pytest.raises(Http404):
        record_desk_payment(hotel_a, booking.pk, user=None, idempotency_key=uuid.uuid4())
    assert not Payment.objects.exists()


# --- close_bill -------------------------------------------------------------

def test_close_bill_issues_the_next_receipt_number_of_the_property(hotel_a, hotel_b, staff):
    first, second = _booking(hotel_a), _booking(hotel_a)
    other_hotel = _booking(hotel_b)
    for booking in (first, second, other_hotel):
        _pay_at_desk(booking)

    receipt_1 = close_bill(hotel_a, first.pk, user=staff)
    receipt_2 = close_bill(hotel_a, second.pk, user=staff)
    receipt_b = close_bill(hotel_b, other_hotel.pk, user=staff)

    assert (receipt_1.number, receipt_2.number, receipt_b.number) == (1, 2, 1)
    assert receipt_1.total == Decimal('2140.00')
    assert receipt_1.tax_rate == Decimal('7.00')
    assert receipt_1.tax_amount == Decimal('140.00')
    assert receipt_1.issued_by == staff
    assert bill_is_closed(hotel_a, first)


def test_close_bill_twice_returns_the_same_receipt(hotel_a, staff):
    booking = _booking(hotel_a)
    _stripe_charge(booking)

    first = close_bill(hotel_a, booking.pk, user=staff)
    second = close_bill(hotel_a, booking.pk, user=staff)

    assert first.pk == second.pk
    assert Receipt.objects.count() == 1


def test_close_bill_with_money_still_due_is_refused(hotel_a, staff):
    booking = _booking(hotel_a)
    _stripe_charge(booking, amount=Decimal('1000.00'))

    with pytest.raises(ValidationError) as error:
        close_bill(hotel_a, booking.pk, user=staff)
    assert error.value.message_dict == {'balance': [BALANCE_DUE_MESSAGE]}
    assert not bill_is_closed(hotel_a, booking)


@pytest.mark.parametrize('status', [Status.CONFIRMED, Status.CHECKED_OUT, Status.CANCELLED])
def test_close_bill_needs_a_checked_in_booking(hotel_a, staff, status):
    booking = _booking(hotel_a, status=status)
    _stripe_charge(booking)

    with pytest.raises(ValidationError) as error:
        close_bill(hotel_a, booking.pk, user=staff)
    assert error.value.message_dict == {'status': [NOT_CHECKED_IN_MESSAGE]}
    assert not Receipt.objects.exists()


def test_receipt_keeps_its_tax_rate_when_the_property_changes_it(hotel_a, staff):
    booking = _booking(hotel_a)
    _pay_at_desk(booking)
    close_bill(hotel_a, booking.pk, user=staff)

    Property.objects.filter(pk=hotel_a.pk).update(tax_rate=Decimal('10.00'))
    hotel_a.refresh_from_db()
    folio = get_folio(hotel_a, booking)

    assert folio.tax_rate == Decimal('7.00')
    assert folio.tax_amount == Decimal('140.00')
    assert folio.receipt.tax_rate == Decimal('7.00')


def test_close_bill_for_a_booking_of_another_property_is_404(hotel_a, hotel_b, staff):
    booking = _booking(hotel_b)
    _stripe_charge(booking)

    with pytest.raises(Http404):
        close_bill(hotel_a, booking.pk, user=staff)
    assert not Receipt.objects.exists()


def test_bill_is_closed_does_not_see_another_property(hotel_a, hotel_b, staff):
    booking = _booking(hotel_b)
    _stripe_charge(booking)
    close_bill(hotel_b, booking.pk, user=staff)

    assert bill_is_closed(hotel_b, booking)
    assert not bill_is_closed(hotel_a, booking)


def test_receipt_cannot_be_edited_or_deleted(hotel_a, staff):
    booking = _booking(hotel_a)
    _stripe_charge(booking)
    receipt = close_bill(hotel_a, booking.pk, user=staff)

    receipt.total = Decimal('1.00')
    with pytest.raises(ValidationError):
        receipt.save()
    with pytest.raises(ValidationError):
        receipt.delete()
    receipt.refresh_from_db()
    assert receipt.total == Decimal('2140.00')