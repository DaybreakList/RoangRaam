from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

import factory
import pytest
from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from psycopg.types.range import DateRange

from apps.bookings.cancellation import cancel_booking
from apps.bookings.folio import close_bill, get_folio
from apps.bookings.models import Booking, BookingRoom, Payment, Receipt
from apps.bookings.payments import PAYMENT_WINDOW
from apps.bookings.tests.factories import BookingFactory, BookingNightRateFactory, BookingRoomFactory
from apps.frontdesk.services.check_in_out import check_in, check_out
from apps.frontdesk.services.tape_chart_moves import ROOM_TAKEN_MESSAGE, move_booking_room
from apps.properties.models import Membership, PropertyScopedQuerySet
from apps.properties.tests.factories import PropertyFactory, UserFactory
from apps.rooms.models import Room
from apps.rooms.tests.factories import RoomFactory, RoomTypeFactory

pytestmark = pytest.mark.django_db


def url(prop, stay):
    return reverse('tape_chart:move', kwargs={'property_slug': prop.slug, 'booking_room_id': stay.pk})


@pytest.fixture
def move_case(hotel_a):
    stay = BookingRoomFactory(booking=BookingFactory(property=hotel_a))
    target = RoomFactory(property=hotel_a, room_type=stay.room_type)
    return stay, target


def state():
    return (
        list(Booking.objects.order_by('pk').values()),
        list(BookingRoom.objects.order_by('pk').values()),
        list(Room.objects.order_by('pk').values()),
        list(Payment.objects.order_by('pk').values()),
        list(Receipt.objects.order_by('pk').values()),
    )


@pytest.mark.parametrize('role', [Membership.Role.OWNER, Membership.Role.MANAGER, Membership.Role.FRONT_DESK])
@pytest.mark.parametrize('cleanliness', ['CLEAN', 'DIRTY'])
def test_move_one_room_keeps_dates_prices_and_other_rooms(client, hotel_a, login_member, move_case, role, cleanliness):
    stay, target = move_case
    login_member(hotel_a, role)
    target.hk_status = cleanliness
    target.save(update_fields=['hk_status'])
    sibling = BookingRoomFactory(booking=stay.booking)
    sibling_room = sibling.room_id
    night = BookingNightRateFactory(booking_room=stay, price=Decimal('1234.56'))
    stay.subtotal = Decimal('1234.56')
    stay.save(update_fields=['subtotal'])
    stay.booking.total_amount = Decimal('1234.56')
    stay.booking.save(update_fields=['total_amount'])
    old_stay = BookingRoom.objects.values().get(pk=stay.pk)
    old_booking = Booking.objects.values().get(pk=stay.booking_id)
    old_rooms = list(Room.objects.order_by('pk').values())
    response = client.post(url(hotel_a, stay), {'target_room_id': target.pk,
        'check_in': '2099-01-01', 'check_out': '2099-12-31', 'subtotal': '0.01'})
    assert response.status_code == 200
    assert response.json()['booking_room_id'] == stay.pk
    assert response.json()['room_id'] == target.pk
    assert response.json()['changed'] is True
    assert BookingRoom.objects.values().get(pk=stay.pk) == {**old_stay, 'room_id': target.pk}
    assert Booking.objects.values().get(pk=stay.booking_id) == old_booking
    assert list(Room.objects.order_by('pk').values()) == old_rooms
    sibling.refresh_from_db()
    night.refresh_from_db()
    assert sibling.room_id == sibling_room
    assert night.price == Decimal('1234.56')
    assert Payment.objects.count() == 0


@pytest.mark.parametrize('offset,allowed', [(-1, True), (0, False), (1, False)])
def test_pending_payment_boundary_uses_payment_window(client, hotel_a, login_member, move_case, offset, allowed):
    stay, target = move_case
    login_member(hotel_a)
    now = timezone.now()
    # A different window proves that the endpoint does not hardcode 60 minutes.
    window = timedelta(minutes=7)
    Booking.objects.filter(pk=stay.booking_id).update(
        status=Booking.Status.PENDING_PAYMENT, created_at=now - window - timedelta(microseconds=offset),
    )
    before = state()
    with patch('apps.bookings.payments.PAYMENT_WINDOW', window), patch('django.utils.timezone.now', return_value=now):
        response = client.post(url(hotel_a, stay), {'target_room_id': target.pk})
    assert response.status_code == (200 if allowed else 400)
    if not allowed:
        assert 'status' in response.json()['errors']
        assert state() == before


@pytest.mark.parametrize('status', ['CHECKED_IN', 'CHECKED_OUT', 'CANCELLED'])
def test_terminal_or_in_house_status_cannot_move(client, hotel_a, login_member, move_case, status):
    stay, target = move_case
    login_member(hotel_a)
    Booking.objects.filter(pk=stay.booking_id).update(status=status)
    before = state()
    response = client.post(url(hotel_a, stay), {'target_room_id': target.pk})
    assert response.status_code == 400
    assert state() == before


@pytest.mark.parametrize('reason', ['inactive_stay', 'inactive_target', 'different_type'])
def test_invalid_move_changes_nothing(client, hotel_a, login_member, move_case, reason):
    stay, target = move_case
    login_member(hotel_a)
    if reason == 'inactive_stay':
        BookingRoom.objects.filter(pk=stay.pk).update(is_active=False)
    elif reason == 'inactive_target':
        target.is_active = False
        target.save(update_fields=['is_active'])
    else:
        target.room_type = RoomTypeFactory(property=hotel_a)
        target.save(update_fields=['room_type'])
    before = state()
    assert client.post(url(hotel_a, stay), {'target_room_id': target.pk}).status_code == 400
    assert state() == before


@pytest.mark.parametrize('same_booking', [False, True])
def test_occupied_target_including_same_booking_is_blocked(client, hotel_a, login_member, move_case, same_booking):
    stay, target = move_case
    login_member(hotel_a)
    booking = stay.booking if same_booking else BookingFactory(
        property=hotel_a, check_in_date=stay.booking.check_in_date, check_out_date=stay.booking.check_out_date,
    )
    BookingRoomFactory(booking=booking, room=target)
    before = state()
    response = client.post(url(hotel_a, stay), {'target_room_id': target.pk})
    assert response.status_code == 400
    assert response.json()['errors']['target_room_id'] == [ROOM_TAKEN_MESSAGE]
    assert state() == before


def test_overlap_outside_fourteen_day_chart_is_blocked(hotel_a, move_case):
    stay, target = move_case
    start = stay.stay_range.lower
    stay.stay_range = DateRange(start, start + timedelta(days=30))
    stay.save(update_fields=['stay_range'])
    BookingRoomFactory(room=target, booking=BookingFactory(property=hotel_a,
        check_in_date=start + timedelta(days=20), check_out_date=start + timedelta(days=21)))
    with pytest.raises(ValidationError):
        move_booking_room(hotel_a, stay.pk, target_room_id=target.pk)
    stay.refresh_from_db()
    assert stay.room_id != target.pk


@pytest.mark.parametrize('released', [False, True])
def test_adjacent_or_released_stay_does_not_block(hotel_a, move_case, released):
    stay, target = move_case
    check_in = stay.stay_range.lower if released else stay.stay_range.upper
    BookingRoomFactory(room=target, is_active=not released, booking=BookingFactory(
        property=hotel_a, check_in_date=check_in, check_out_date=check_in + timedelta(days=2)))
    _, changed = move_booking_room(hotel_a, stay.pk, target_room_id=target.pk)
    assert changed is True


def test_expired_but_unreleased_target_hold_still_blocks(hotel_a, move_case):
    stay, target = move_case
    hold = BookingFactory(property=hotel_a, status=Booking.Status.PENDING_PAYMENT,
        check_in_date=stay.stay_range.lower, check_out_date=stay.stay_range.upper)
    Booking.objects.filter(pk=hold.pk).update(created_at=timezone.now() - PAYMENT_WINDOW - timedelta(seconds=1))
    BookingRoomFactory(booking=hold, room=target)
    before = state()
    with pytest.raises(ValidationError):
        move_booking_room(hotel_a, stay.pk, target_room_id=target.pk)
    assert state() == before


def test_repeat_move_is_noop(client, hotel_a, login_member, move_case):
    stay, target = move_case
    login_member(hotel_a)
    assert client.post(url(hotel_a, stay), {'target_room_id': target.pk}).status_code == 200
    before = state()
    with CaptureQueriesContext(connection) as queries:
        response = client.post(url(hotel_a, stay), {'target_room_id': target.pk})
    assert response.status_code == 200
    assert response.json()['changed'] is False
    assert state() == before
    assert not any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries)


@pytest.mark.parametrize('foreign', ['property_url', 'stay_url', 'target_body', 'missing_stay', 'missing_target'])
def test_foreign_or_missing_ids_return_404_without_changes(client, hotel_a, hotel_b, login_member, move_case, foreign):
    stay, target = move_case
    login_member(hotel_a)
    other = BookingRoomFactory(booking=BookingFactory(property=hotel_b))
    route = url(hotel_a, stay)
    data = {'target_room_id': target.pk}
    if foreign == 'property_url':
        route = url(hotel_b, other)
    elif foreign == 'stay_url':
        route = url(hotel_a, other)
    elif foreign == 'target_body':
        data['target_room_id'] = other.room_id
    elif foreign == 'missing_stay':
        route = reverse('tape_chart:move', kwargs={'property_slug': hotel_a.slug, 'booking_room_id': 999999999})
    else:
        data['target_room_id'] = 999999999
    before = state()
    with CaptureQueriesContext(connection) as queries:
        response = client.post(route, data)
    assert response.status_code == 404
    assert state() == before
    assert not any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries)


@pytest.mark.parametrize('field', ['booking', 'room', 'room_type'])
def test_inconsistent_foreign_related_id_is_404(client, hotel_a, hotel_b, login_member, move_case, field):
    stay, target = move_case
    login_member(hotel_a)
    other = BookingRoomFactory(booking=BookingFactory(property=hotel_b), is_active=False)
    BookingRoom.objects.filter(pk=stay.pk).update(**{field + '_id': getattr(other, field + '_id')})
    before = state()
    assert client.post(url(hotel_a, stay), {'target_room_id': target.pk}).status_code == 404
    assert state() == before


@pytest.mark.parametrize('value', [None, '', 'abc', '1.5', '-1', '0', '999999999999999999999999999'])
def test_invalid_body_is_400(client, hotel_a, login_member, move_case, value):
    stay, _ = move_case
    login_member(hotel_a)
    before = state()
    response = client.post(url(hotel_a, stay), {} if value is None else {'target_room_id': value})
    assert response.status_code == 400
    assert 'target_room_id' in response.json()['errors']
    assert state() == before


def test_housekeeping_is_forbidden(client, hotel_a, login_member, move_case):
    stay, target = move_case
    login_member(hotel_a, Membership.Role.HOUSEKEEPING)
    before = state()
    assert client.post(url(hotel_a, stay), {'target_room_id': target.pk}).status_code == 403
    assert state() == before


def test_anonymous_redirects(client, hotel_a, move_case):
    stay, target = move_case
    before = state()
    response = client.post(url(hotel_a, stay), {'target_room_id': target.pk})
    assert response.status_code == 302
    assert '/accounts/login/' in response.url
    assert state() == before


@pytest.mark.parametrize('method', ['get', 'head', 'put', 'patch', 'delete'])
def test_unsupported_methods_do_not_move(client, hotel_a, login_member, move_case, method):
    stay, target = move_case
    login_member(hotel_a)
    before = state()
    assert getattr(client, method)(url(hotel_a, stay), {'target_room_id': target.pk}).status_code == 405
    assert state() == before


def test_csrf_is_required_and_valid_token_works(client, hotel_a, login_member, move_case):
    stay, target = move_case
    membership = login_member(hotel_a)
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(membership.user)
    before = state()
    assert strict.post(url(hotel_a, stay), {'target_room_id': target.pk}).status_code == 403
    assert state() == before
    strict.get(reverse('tape_chart:index', kwargs={'property_slug': hotel_a.slug}))
    token = strict.cookies['csrftoken'].value
    assert strict.post(url(hotel_a, stay), {'target_room_id': target.pk}, HTTP_X_CSRFTOKEN=token).status_code == 200


def test_real_exclusion_constraint_rolls_back_and_returns_thai_error(hotel_a, move_case):
    stay, target = move_case
    BookingRoomFactory(room=target, booking=BookingFactory(property=hotel_a,
        check_in_date=stay.stay_range.lower, check_out_date=stay.stay_range.upper))
    before = state()
    # Bypass the advisory pre-check, but exercise the real PostgreSQL constraint.
    with transaction.atomic():
        with patch.object(PropertyScopedQuerySet, 'exists', return_value=False):
            with pytest.raises(ValidationError) as error:
                move_booking_room(hotel_a, stay.pk, target_room_id=target.pk)
        assert error.value.message_dict == {'target_room_id': [ROOM_TAKEN_MESSAGE]}
        assert state() == before  # The surrounding transaction remains usable.


def race(*actions):
    barrier = Barrier(len(actions))

    def run(action):
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '5s'")
                cursor.execute("SET statement_timeout = '10s'")
            barrier.wait(timeout=10)
            try:
                action()
                return 'ok'
            except ValidationError:
                return 'invalid'
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        return list(pool.map(run, actions))


@pytest.mark.django_db(transaction=True)
def test_concurrent_moves_to_one_room_have_one_winner():
    prop = PropertyFactory()
    first = BookingRoomFactory(booking=BookingFactory(property=prop))
    second = BookingRoomFactory(booking=BookingFactory(property=prop),
        room=RoomFactory(property=prop, room_type=first.room_type))
    target = RoomFactory(property=prop, room_type=first.room_type)
    results = race(
        lambda: move_booking_room(prop, first.pk, target_room_id=target.pk),
        lambda: move_booking_room(prop, second.pk, target_room_id=target.pk),
    )
    assert sorted(results) == ['invalid', 'ok']
    assert BookingRoom.objects.filter(room=target, is_active=True).count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize('operation', ['cancel', 'check_in', 'check_out'])
def test_move_serializes_with_booking_lifecycle(operation):
    prop = PropertyFactory()
    today = timezone.localdate()
    user = UserFactory()
    booking = BookingFactory(property=prop, check_in_date=today, check_out_date=today + timedelta(days=2))
    source = RoomFactory(property=prop, hk_status='CLEAN')
    target = RoomFactory(property=prop, room_type=source.room_type, hk_status='CLEAN')
    stay = BookingRoomFactory(booking=booking, room=source)
    if operation == 'cancel':
        def action():
            return cancel_booking(prop, booking.pk)
    elif operation == 'check_in':
        def action():
            return check_in(prop, booking.pk, user=user, id_type='PASSPORT', id_number='TEST123', today=today)
    else:
        booking.status = Booking.Status.CHECKED_IN
        booking.save(update_fields=['status'])
        close_bill(prop, booking.pk, user=user)  # check_out() needs a closed bill
        def action():
            return check_out(prop, booking.pk, today=today + timedelta(days=1))
    results = race(lambda: move_booking_room(prop, stay.pk, target_room_id=target.pk), action)
    assert results[1] == 'ok'
    assert results[0] in ('ok', 'invalid')
    stay.refresh_from_db()
    booking.refresh_from_db()
    assert booking.status == {'cancel': 'CANCELLED', 'check_in': 'CHECKED_IN', 'check_out': 'CHECKED_OUT'}[operation]
    if operation == 'cancel':
        assert stay.is_active is False
    elif operation == 'check_in':
        assert stay.checked_in_at is not None
    else:
        assert results[0] == 'invalid'
        assert stay.room_id == source.pk
        source.refresh_from_db()
        assert source.hk_status == 'DIRTY'


class MovePaymentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Payment

    booking = factory.SubFactory(BookingFactory)
    property = factory.SelfAttribute('booking.property')
    amount = Decimal('100.00')
    kind = Payment.Kind.CHARGE
    source = Payment.Source.STRIPE
    provider_ref = factory.Sequence(lambda n: f'pi_move_test_{n}')
    idempotency_key = factory.Sequence(lambda n: f'evt_move_test_{n}')


@pytest.mark.parametrize('closed', [False, True])
def test_move_preserves_folio_money_and_closed_receipts(client, hotel_a, login_member, move_case, closed):
    stay, target = move_case
    login_member(hotel_a)
    booking = stay.booking
    booking.total_amount = Decimal('100.00')
    booking.status = Booking.Status.CHECKED_IN if closed else Booking.Status.CONFIRMED
    booking.save(update_fields=['total_amount', 'status'])
    stay.subtotal = Decimal('100.00')
    stay.save(update_fields=['subtotal'])
    for offset in range(2):
        BookingNightRateFactory(booking_room=stay, night=booking.check_in_date + timedelta(days=offset),
                                price=Decimal('50.00'))
    MovePaymentFactory(booking=booking)
    if closed:
        close_bill(hotel_a, booking.pk, user=None)
    before = get_folio(hotel_a, booking)
    old_state = state()
    old_payments = list(Payment.objects.values())
    old_receipts = list(Receipt.objects.values())
    old_rates = list(stay.night_rates.values())

    response = client.post(url(hotel_a, stay), {'target_room_id': target.pk})

    assert response.status_code == (400 if closed else 200)
    after = get_folio(hotel_a, booking)
    for field in ('total', 'tax_rate', 'tax_amount', 'net_amount', 'paid', 'balance', 'receipt'):
        assert getattr(after, field) == getattr(before, field)
    assert [(line.night, line.price, line.room_type_name) for line in after.lines] == [
        (line.night, line.price, line.room_type_name) for line in before.lines
    ]
    assert list(Payment.objects.values()) == old_payments
    assert list(Receipt.objects.values()) == old_receipts
    assert list(stay.night_rates.values()) == old_rates
    if closed:
        assert state() == old_state
    else:
        assert all(line.room_number == target.number for line in after.lines)
        assert after.balance == Decimal('0.00')
