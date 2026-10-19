from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
import stripe
from django.core.exceptions import ValidationError
from django.http import Http404
from django.utils import timezone

from apps.bookings.cancellation import cancel_booking
from apps.bookings.models import Booking, BookingRoom, Payment
from apps.bookings.payments import REFUND_FAILED_MESSAGE
from apps.bookings.services import get_available_room_types
from apps.rooms.tests.factories import DailyRateFactory, RoomFactory

from .factories import BookingFactory, BookingRoomFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def booking(hotel_a):
    """A confirmed booking of hotel A holding two rooms."""
    booking = BookingFactory(property=hotel_a)
    BookingRoomFactory(booking=booking)
    BookingRoomFactory(booking=booking)
    return booking


def test_cancel_marks_booking_and_releases_rooms(hotel_a, booking):
    cancel_booking(hotel_a, booking.pk)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    assert booking.cancelled_at is not None
    assert not booking.booking_rooms.filter(is_active=True).exists()


def test_pending_payment_booking_can_be_cancelled(hotel_a):
    booking = BookingFactory(property=hotel_a, status=Booking.Status.PENDING_PAYMENT)
    cancel_booking(hotel_a, booking.pk)
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED


def test_released_room_shows_in_search_and_can_be_booked_again(hotel_a):
    room = RoomFactory(property=hotel_a)
    check_in = timezone.localdate() + timedelta(days=7)
    check_out = check_in + timedelta(days=2)
    for offset in range(2):
        DailyRateFactory(property=hotel_a, room_type=room.room_type, date=check_in + timedelta(days=offset))
    booking = BookingFactory(property=hotel_a, check_in_date=check_in, check_out_date=check_out)
    BookingRoomFactory(booking=booking, room=room)
    assert room.room_type not in get_available_room_types(hotel_a, check_in, check_out)

    cancel_booking(hotel_a, booking.pk)

    assert room.room_type in get_available_room_types(hotel_a, check_in, check_out)
    # The exclusion constraint must no longer block the same room and dates.
    again = BookingFactory(property=hotel_a, check_in_date=check_in, check_out_date=check_out)
    BookingRoomFactory(booking=again, room=room)


@pytest.mark.parametrize('status', [
    Booking.Status.CHECKED_IN,
    Booking.Status.CHECKED_OUT,
    Booking.Status.CANCELLED,
])
def test_booking_past_confirmation_cannot_be_cancelled(hotel_a, status):
    booking = BookingFactory(property=hotel_a, status=status)
    booking_room = BookingRoomFactory(booking=booking)

    with pytest.raises(ValidationError) as error:
        cancel_booking(hotel_a, booking.pk)

    assert 'status' in error.value.message_dict

    booking.refresh_from_db()
    booking_room.refresh_from_db()
    assert booking.status == status
    assert booking_room.is_active


def test_other_bookings_keep_their_rooms(hotel_a, booking):
    other = BookingFactory(property=hotel_a)
    other_room = BookingRoomFactory(booking=other)

    cancel_booking(hotel_a, booking.pk)

    other.refresh_from_db()
    other_room.refresh_from_db()
    assert other.status == Booking.Status.CONFIRMED
    assert other_room.is_active


def test_other_hotel_cannot_cancel(hotel_b, booking):
    with pytest.raises(Http404):
        cancel_booking(hotel_b, booking.pk)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.cancelled_at is None
    assert BookingRoom.objects.filter(booking=booking, is_active=True).count() == 2


@pytest.fixture
def stripe_refund():
    with mock.patch('apps.bookings.payments.stripe.Refund.create') as create:
        create.return_value = mock.Mock(id='re_test_1')
        yield create


def _payment(booking, kind=Payment.Kind.CHARGE, ref='pi_1', key=None):
    return Payment.scoped.for_property(booking.property).create(
        property=booking.property, booking=booking, kind=kind, amount=Decimal('3600.00'),
        provider_ref=ref, idempotency_key=key or f'evt-{ref}',
    )


def _payments(booking):
    return sorted(
        Payment.scoped.for_property(booking.property).filter(booking=booking)
        .values_list('kind', 'amount', 'provider_ref'),
    )


def test_cancel_paid_booking_refunds_in_full(hotel_a, booking, stripe_refund):
    _payment(booking)

    cancel_booking(hotel_a, booking.pk)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    stripe_refund.assert_called_once_with(
        api_key=mock.ANY, payment_intent='pi_1', idempotency_key='refund-pi_1',
    )
    assert _payments(booking) == [
        (Payment.Kind.CHARGE, Decimal('3600.00'), 'pi_1'),
        (Payment.Kind.REFUND, Decimal('3600.00'), 're_test_1'),
    ]


def test_unpaid_booking_is_cancelled_without_stripe(hotel_a, booking, stripe_refund):
    cancel_booking(hotel_a, booking.pk)

    stripe_refund.assert_not_called()
    assert _payments(booking) == []


def test_failed_refund_keeps_booking_and_can_be_retried(hotel_a, booking, stripe_refund):
    _payment(booking)
    stripe_refund.side_effect = stripe.APIConnectionError('down')

    with pytest.raises(ValidationError) as error:
        cancel_booking(hotel_a, booking.pk)

    assert error.value.message_dict == {'status': [REFUND_FAILED_MESSAGE]}
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.booking_rooms.filter(is_active=True).count() == 2
    assert [kind for kind, _, _ in _payments(booking)] == [Payment.Kind.CHARGE]

    stripe_refund.side_effect = None
    cancel_booking(hotel_a, booking.pk)

    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED
    assert [kind for kind, _, _ in _payments(booking)] == [Payment.Kind.CHARGE, Payment.Kind.REFUND]


def test_charge_refunded_earlier_is_not_refunded_again(hotel_a, booking, stripe_refund):
    # A second Checkout page paid too and the webhook already gave that money back
    _payment(booking, ref='pi_1')
    _payment(booking, ref='pi_2')
    _payment(booking, kind=Payment.Kind.REFUND, ref='re_old', key='refund-pi_2')

    cancel_booking(hotel_a, booking.pk)

    stripe_refund.assert_called_once_with(
        api_key=mock.ANY, payment_intent='pi_1', idempotency_key='refund-pi_1',
    )


def test_other_hotel_cannot_cancel_paid_booking(hotel_b, booking, stripe_refund):
    _payment(booking)

    with pytest.raises(Http404):
        cancel_booking(hotel_b, booking.pk)

    stripe_refund.assert_not_called()
    assert [kind for kind, _, _ in _payments(booking)] == [Payment.Kind.CHARGE]
