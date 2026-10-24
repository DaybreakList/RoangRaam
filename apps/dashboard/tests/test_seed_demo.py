from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import CommandError, call_command
from django.db.models import Min
from django.utils import timezone

from apps.bookings.cancellation import cancel_booking
from apps.bookings.models import Booking, BookingNightRate, BookingRoom, Guest, Payment
from apps.bookings.tests.factories import BookingRoomFactory
from apps.dashboard import seed
from apps.properties.models import Membership, Property
from apps.properties.tests.factories import MembershipFactory, PropertyFactory
# Deleting a Property makes Django look into every model that points at it, including
# the test-only ScopedThing registered by apps/properties tests. Its table exists only
# while this fixture is active, so tests that delete a hotel must request it.
from apps.properties.tests.scoped_model import scoped_thing_table  # noqa: F401
from apps.rooms.models import DailyRate, Room

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = 'demo-password-for-tests'
Role = Membership.Role


def run_seed(*args):
    output = StringIO()
    call_command('seed_demo', *args, stdout=output)
    return output.getvalue()


@pytest.fixture
def demo_password(monkeypatch):
    monkeypatch.setenv('SEED_DEMO_PASSWORD', PASSWORD)
    return PASSWORD


@pytest.fixture
def small_windows(monkeypatch, demo_password):
    """Shrink the time windows so the suite stays fast. The logic is unchanged."""
    monkeypatch.setattr(seed, 'HISTORY_DAYS', 14)
    monkeypatch.setattr(seed, 'RATES_PAST_DAYS', 20)
    monkeypatch.setattr(seed, 'RATES_FUTURE_DAYS', 40)
    monkeypatch.setattr(seed, 'FUTURE_DAYS', 14)
    monkeypatch.setattr(seed, 'FUTURE_ATTEMPTS_PER_ROOM', 2)


def test_seed_creates_two_hotels_with_staff_and_todays_activity(demo_password):
    run_seed()
    today = timezone.localdate()
    riverside = Property.objects.get(slug='demo-riverside')
    hilltop = Property.objects.get(slug='demo-hilltop')

    assert Room.scoped.for_property(riverside).count() == 12
    assert Room.scoped.for_property(hilltop).count() == 10
    numbers = [
        set(Room.scoped.for_property(hotel).values_list('number', flat=True))
        for hotel in (riverside, hilltop)
    ]
    assert numbers[0] & numbers[1]  # room numbers repeat across hotels

    owner = User.objects.get(email='owner@example.com')
    assert owner.check_password(demo_password)
    assert set(Membership.objects.filter(user=owner).values_list('property__slug', 'role')) == {
        ('demo-riverside', Role.OWNER), ('demo-hilltop', Role.OWNER),
    }

    for hotel in (riverside, hilltop):
        roles = set(Membership.objects.filter(property=hotel).values_list('role', flat=True))
        assert roles == {Role.OWNER, Role.MANAGER, Role.FRONT_DESK, Role.HOUSEKEEPING}

        bookings = Booking.scoped.for_property(hotel)
        arrivals = bookings.filter(check_in_date=today).exclude(status=Booking.Status.CANCELLED)
        departures = bookings.filter(check_out_date=today, status=Booking.Status.CHECKED_IN)
        in_house = bookings.filter(
            status=Booking.Status.CHECKED_IN, check_in_date__lt=today, check_out_date__gt=today,
        )
        assert arrivals.count() >= 2
        assert departures.count() >= 2
        assert in_house.count() >= 3

        assert bookings.filter(status=Booking.Status.CHECKED_OUT).exists()
        assert bookings.filter(status=Booking.Status.CANCELLED).exists()
        assert bookings.filter(check_in_date__gt=today).exists()
        assert bookings.aggregate(first=Min('check_in_date'))['first'] <= today - timedelta(days=50)

        # Rooms that check out today are waiting to be cleaned
        for booking in departures:
            for booking_room in booking.booking_rooms.all():
                assert booking_room.room.hk_status == Room.HousekeepingStatus.DIRTY


def test_bookings_are_consistent_never_overlap_and_stay_in_their_hotel(small_windows):
    run_seed()
    assert mail.outbox == []  # seeding never sends email

    for hotel in Property.objects.filter(slug__in=seed.SEED_SLUGS):
        rates = {
            (rate.room_type_id, rate.date): rate.price
            for rate in DailyRate.scoped.for_property(hotel)
        }

        # No two active stays of one room overlap (ranges are [check_in, check_out))
        active = sorted(
            BookingRoom.scoped.for_property(hotel).filter(is_active=True),
            key=lambda booking_room: (booking_room.room_id, booking_room.stay_range.lower),
        )
        last_end = {}
        for booking_room in active:
            lower, upper = booking_room.stay_range.lower, booking_room.stay_range.upper
            assert booking_room.room_id not in last_end or last_end[booking_room.room_id] <= lower
            last_end[booking_room.room_id] = upper

        # Money: per-night snapshots add up to subtotals, subtotals to the booking total
        bookings = Booking.scoped.for_property(hotel).prefetch_related('booking_rooms__night_rates')
        assert bookings.exists()
        for booking in bookings:
            total = Decimal('0.00')
            for booking_room in booking.booking_rooms.all():
                nights = (booking_room.stay_range.upper - booking_room.stay_range.lower).days
                night_rates = list(booking_room.night_rates.all())
                assert len(night_rates) == nights
                assert all(isinstance(rate.price, Decimal) for rate in night_rates)
                assert booking_room.subtotal == sum((rate.price for rate in night_rates), Decimal('0.00'))
                for rate in night_rates:
                    assert rate.price == rates[booking_room.room_type_id, rate.night]
                total += booking_room.subtotal
            assert booking.total_amount == total

        # Isolation: every related row belongs to the same hotel
        booking_rooms = BookingRoom.scoped.for_property(hotel)
        assert not booking_rooms.exclude(booking__property=hotel).exists()
        assert not booking_rooms.exclude(room__property=hotel).exists()
        assert not booking_rooms.exclude(room_type__property=hotel).exists()
        assert not Booking.scoped.for_property(hotel).exclude(guest__property=hotel).exists()
        assert not BookingNightRate.scoped.for_property(hotel).exclude(
            booking_room__property=hotel,
        ).exists()


def test_running_twice_does_not_duplicate_data(small_windows):
    run_seed()
    before = (
        Property.objects.count(), Room.objects.count(), Booking.objects.count(),
        Guest.objects.count(), User.objects.count(),
    )

    output = run_seed()

    assert 'มีข้อมูลเดโมอยู่แล้ว' in output
    after = (
        Property.objects.count(), Room.objects.count(), Booking.objects.count(),
        Guest.objects.count(), User.objects.count(),
    )
    assert after == before


def test_reset_recreates_demo_data_and_leaves_other_hotels_alone(
    small_windows, scoped_thing_table,
):
    other = PropertyFactory(slug='other-hotel')
    other_booking_room = BookingRoomFactory(booking__property=other)
    run_seed()
    demo_bookings = Booking.objects.filter(property__slug__in=seed.SEED_SLUGS).count()
    demo_payments = Payment.objects.filter(property__slug__in=seed.SEED_SLUGS).count()
    assert demo_payments > 0

    run_seed('--reset')

    assert Property.objects.filter(slug__in=seed.SEED_SLUGS).count() == 2
    assert Booking.objects.filter(property__slug__in=seed.SEED_SLUGS).count() == demo_bookings
    assert Payment.objects.filter(property__slug__in=seed.SEED_SLUGS).count() == demo_payments
    assert Property.objects.filter(pk=other.pk).exists()
    assert BookingRoom.objects.filter(pk=other_booking_room.pk).exists()


def test_reset_keeps_demo_users_who_also_belong_to_another_hotel(
    small_windows, scoped_thing_table,
):
    run_seed()
    other = PropertyFactory(slug='other-hotel')
    owner = User.objects.get(email='owner@example.com')
    MembershipFactory(user=owner, property=other)
    assert Payment.objects.exists()

    removed = seed.reset_demo_data()

    assert removed == 2
    # Payment rows are append-only, yet a reset removes the demo hotels' money with them
    assert not Payment.objects.exists()
    assert not Property.objects.filter(slug__in=seed.SEED_SLUGS).exists()
    assert not Booking.objects.filter(property__slug__in=seed.SEED_SLUGS).exists()
    assert User.objects.filter(email='owner@example.com').exists()
    assert not User.objects.filter(email='manager.riverside@example.com').exists()
    assert Membership.objects.filter(user=owner, property=other).exists()


def test_future_bookings_are_pay_at_hotel_and_hold_no_money(small_windows):
    run_seed()
    today = timezone.localdate()
    demo = Booking.objects.filter(property__slug__in=seed.SEED_SLUGS)
    future = demo.filter(check_in_date__gte=today)

    assert future.exists()
    assert not future.exclude(payment_option=Booking.PaymentOption.PAY_AT_HOTEL).exists()
    # A FULL booking waiting for Stripe would be released after the payment window
    assert not demo.filter(status=Booking.Status.PENDING_PAYMENT).exists()
    assert not Payment.objects.filter(booking__in=future).exists()


def test_past_full_bookings_carry_the_money_a_real_payment_leaves(small_windows):
    run_seed()

    for hotel in Property.objects.filter(slug__in=seed.SEED_SLUGS):
        full = Booking.scoped.for_property(hotel).filter(
            payment_option=Booking.PaymentOption.FULL,
        ).prefetch_related('payments')
        assert full.exists()
        for booking in full:
            assert booking.status in (
                Booking.Status.CHECKED_OUT, Booking.Status.CHECKED_IN, Booking.Status.CANCELLED,
            )
            charges = [p for p in booking.payments.all() if p.kind == Payment.Kind.CHARGE]
            refunds = [p for p in booking.payments.all() if p.kind == Payment.Kind.REFUND]
            assert len(charges) == 1
            assert charges[0].amount == booking.total_amount
            assert charges[0].provider_ref.startswith('seed_')
            if booking.status == Booking.Status.CANCELLED:
                # Same shape refund_charge() writes, so a repeat can never refund twice
                assert len(refunds) == 1
                assert refunds[0].amount == charges[0].amount
                assert refunds[0].idempotency_key == f'refund-{charges[0].provider_ref}'
                assert charges[0].created_at < refunds[0].created_at
            else:
                assert refunds == []

        payments = Payment.scoped.for_property(hotel)
        # Pay at hotel: the system records no cash
        assert not payments.filter(booking__payment_option=Booking.PaymentOption.PAY_AT_HOTEL).exists()
        # Isolation: money only points at bookings of its own hotel
        assert not payments.exclude(booking__property=hotel).exists()


def test_cancelling_a_seeded_future_booking_never_calls_stripe(small_windows):
    run_seed()
    booking = Booking.objects.filter(
        property__slug='demo-riverside',
        check_in_date__gte=timezone.localdate(),
        status=Booking.Status.CONFIRMED,
    ).select_related('property').first()

    with patch('stripe.Refund.create') as stripe_refund:
        cancel_booking(booking.property, booking.pk)

    stripe_refund.assert_not_called()
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CANCELLED


def test_seed_requires_a_password_from_the_environment(monkeypatch):
    monkeypatch.delenv('SEED_DEMO_PASSWORD', raising=False)

    with pytest.raises(CommandError):
        run_seed()

    assert not Property.objects.filter(slug__in=seed.SEED_SLUGS).exists()
