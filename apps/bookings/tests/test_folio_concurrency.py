"""
Bills closed at the same instant against a real PostgreSQL transaction.

Each thread has its own DB connection, so the property lock in close_bill()
behaves as in production: receipt numbers never repeat or skip.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from django.db import connection

from apps.bookings.folio import close_bill, record_desk_payment
from apps.bookings.models import Booking, Payment, Receipt
from apps.properties.tests.factories import PropertyFactory

from .factories import BookingFactory

pytestmark = pytest.mark.django_db(transaction=True)

THREADS = 10
TOTAL = Decimal('1000.00')


def _run_together(calls):
    barrier = threading.Barrier(len(calls))

    def run(call):
        try:
            barrier.wait()
            return call()
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        return list(pool.map(run, calls))


def test_bills_closed_at_once_get_numbers_1_to_n():
    prop = PropertyFactory()
    bookings = [
        BookingFactory(property=prop, status=Booking.Status.CHECKED_IN, total_amount=TOTAL)
        for _ in range(THREADS)
    ]
    for booking in bookings:
        record_desk_payment(prop, booking.pk, user=None, idempotency_key=uuid.uuid4())

    _run_together([lambda pk=booking.pk: close_bill(prop, pk, user=None) for booking in bookings])

    assert sorted(Receipt.objects.values_list('number', flat=True)) == list(range(1, THREADS + 1))


def test_one_desk_payment_sent_at_once_is_recorded_once():
    prop = PropertyFactory()
    booking = BookingFactory(property=prop, status=Booking.Status.CHECKED_IN, total_amount=TOTAL)
    key = uuid.uuid4()

    _run_together([
        lambda: record_desk_payment(prop, booking.pk, user=None, idempotency_key=key)
    ] * THREADS)

    assert Payment.objects.filter(booking=booking).count() == 1