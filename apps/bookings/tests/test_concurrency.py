"""
50 simultaneous booking requests against a real PostgreSQL transaction.

Each thread has its own DB connection, so select_for_update() locks and the
exclusion constraint behave exactly as in production.
"""

import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from apps.bookings.models import Booking, BookingRoom
from apps.properties.tests.factories import PropertyFactory
from apps.rooms.tests.factories import DailyRateFactory, RoomFactory, RoomTypeFactory

pytestmark = pytest.mark.django_db(transaction=True)

REQUESTS = 50


def day(offset):
    return timezone.localdate() + timedelta(days=offset)


def make_room_type(prop, rooms, name):
    room_type = RoomTypeFactory(property=prop, name=name, max_occupancy=2)
    for i in range(rooms):
        RoomFactory(property=prop, room_type=room_type, number=f'{name}-{i + 1:02d}')
    for offset in range(3):
        DailyRateFactory(property=prop, room_type=room_type, date=day(10 + offset), price=Decimal('1000.00'))
    return room_type


def fire(prop, payloads):
    """Send every payload at the same instant from its own thread; return status codes."""
    url = reverse('bookings:api-booking-create', kwargs={'property_slug': prop.slug})
    barrier = threading.Barrier(len(payloads))

    def send(data):
        try:
            client = Client()
            barrier.wait()
            return client.post(url, data, content_type='application/json').status_code
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(payloads)) as pool:
        return Counter(pool.map(send, payloads))


def payload(rooms):
    return {
        'check_in': day(10).isoformat(),
        'check_out': day(13).isoformat(),
        'adults': 1,
        'payment_option': 'FULL',
        'guest': {'first_name': 'Race', 'last_name': 'Test', 'email': 'race@example.com', 'phone': '0800000000'},
        'rooms': [{'room_type_id': rt.pk, 'quantity': 1} for rt in rooms],
    }


def assert_no_overlaps():
    active = BookingRoom.objects.filter(is_active=True)
    assert active.count() == active.values('room').distinct().count()


def test_50_requests_for_3_rooms_book_exactly_3():
    prop = PropertyFactory()
    twin = make_room_type(prop, rooms=3, name='Twin')

    statuses = fire(prop, [payload([twin])] * REQUESTS)

    assert statuses == {201: 3, 400: REQUESTS - 3}
    assert Booking.objects.count() == 3
    assert BookingRoom.objects.count() == 3
    assert_no_overlaps()


def test_50_requests_for_last_room_book_exactly_1():
    prop = PropertyFactory()
    single = make_room_type(prop, rooms=1, name='Single')

    statuses = fire(prop, [payload([single])] * REQUESTS)

    assert statuses == {201: 1, 400: REQUESTS - 1}
    assert BookingRoom.objects.count() == 1


def test_opposite_type_orders_do_not_deadlock():
    """Half the requests ask for [A, B], half for [B, A]; locks are taken in pk order."""
    prop = PropertyFactory()
    type_a = make_room_type(prop, rooms=10, name='A')
    type_b = make_room_type(prop, rooms=10, name='B')
    payloads = [payload([type_a, type_b]) if i % 2 else payload([type_b, type_a]) for i in range(REQUESTS)]

    statuses = fire(prop, payloads)

    # 500 would mean a deadlock or an unhandled IntegrityError
    assert statuses == {201: 10, 400: REQUESTS - 10}
    assert BookingRoom.objects.count() == 20
    assert_no_overlaps()


def test_other_property_is_unaffected_by_the_race():
    hotel_a = PropertyFactory()
    hotel_b = PropertyFactory()
    twin_a = make_room_type(hotel_a, rooms=2, name='Twin')
    twin_b = make_room_type(hotel_b, rooms=2, name='Twin')

    statuses_a = fire(hotel_a, [payload([twin_a])] * (REQUESTS // 2))
    statuses_b = fire(hotel_b, [payload([twin_b])] * (REQUESTS // 2))

    assert statuses_a[201] == 2 and statuses_b[201] == 2
    assert Booking.objects.filter(property=hotel_a).count() == 2
    assert Booking.objects.filter(property=hotel_b).count() == 2
    assert not BookingRoom.objects.filter(property=hotel_a, room__property=hotel_b).exists()


def test_simultaneous_bookings_with_a_new_email_share_one_guest():
    """
    Same not-yet-known email, each request for a different room type, so no
    RoomType lock serializes them and the guest inserts really race.
    """
    from apps.bookings.models import Guest

    prop = PropertyFactory()
    room_types = [make_room_type(prop, rooms=1, name=f'T{i}') for i in range(10)]

    statuses = fire(prop, [payload([room_type]) for room_type in room_types])

    assert statuses == {201: 10}
    assert Guest.objects.filter(property=prop).count() == 1
    assert Booking.objects.filter(guest__email='race@example.com').count() == 10


def test_double_submit_of_the_last_room_returns_one_booking_to_everyone():
    """Same idempotency key x10 for a type with one room: the waiters must get the original."""
    prop = PropertyFactory()
    single = make_room_type(prop, rooms=1, name='Single')
    data = {**payload([single]), 'idempotency_key': '3f6c1b2a-8d4e-4f7a-9b0c-1d2e3f4a5b6c'}

    statuses = fire(prop, [data] * 10)

    assert statuses == {201: 10}
    assert Booking.objects.count() == 1


def test_double_submit_racing_on_different_room_types_returns_one_booking():
    """Same key, different types: no shared lock, so the unique constraint settles it."""
    prop = PropertyFactory()
    room_types = [make_room_type(prop, rooms=1, name=f'T{i}') for i in range(10)]
    key = '3f6c1b2a-8d4e-4f7a-9b0c-1d2e3f4a5b6c'

    statuses = fire(prop, [{**payload([rt]), 'idempotency_key': key} for rt in room_types])

    assert statuses == {201: 10}
    assert Booking.objects.count() == 1
    assert BookingRoom.objects.count() == 1
