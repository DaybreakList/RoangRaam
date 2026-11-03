import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.bookings.models import Booking
from apps.bookings.payments import PAYMENT_WINDOW
from apps.bookings.tests.factories import BookingFactory, BookingRoomFactory
from apps.frontdesk.services.tape_chart import build_tape_chart
from apps.properties.models import Membership
from apps.rooms.tests.factories import RoomFactory

pytestmark = pytest.mark.django_db


def url(prop):
    return reverse('tape_chart:index', kwargs={'property_slug': prop.slug})


def payload(response):
    content = response.content.decode()
    return json.loads(content.split('<script id="tc-move-data" type="application/json">')[1].split('</script>')[0])


@pytest.mark.parametrize('partial', [False, True])
def test_distinct_booking_room_ids_and_scoped_urls(client, hotel_a, login_member, partial):
    login_member(hotel_a)
    booking = BookingFactory(property=hotel_a)
    first = BookingRoomFactory(booking=booking)
    second = BookingRoomFactory(booking=booking)
    response = client.get(url(hotel_a), HTTP_HX_REQUEST='true' if partial else 'false')
    data = payload(response)
    assert set(data['stays']) == {str(first.pk), str(second.pk)}
    for stay in (first, second):
        assert data['stays'][str(stay.pk)]['url'] == reverse('tape_chart:move', kwargs={
            'property_slug': hotel_a.slug, 'booking_room_id': stay.pk,
        })
        assert f'data-booking-room-id="{stay.pk}"' in response.content.decode()
    assert 'csrfmiddlewaretoken' in response.content.decode()


@pytest.mark.parametrize('status,age,allowed', [
    ('CONFIRMED', None, True), ('PENDING_PAYMENT', -1, True),
    ('PENDING_PAYMENT', 0, False), ('PENDING_PAYMENT', 1, False),
    ('CHECKED_IN', None, False), ('CHECKED_OUT', None, False), ('CANCELLED', None, False),
])
def test_move_eligibility_and_expiry_boundary(hotel_a, status, age, allowed):
    now = timezone.now()
    booking = BookingFactory(property=hotel_a, status=status)
    stay = BookingRoomFactory(booking=booking)
    if age is not None:
        Booking.objects.filter(pk=booking.pk).update(created_at=now - PAYMENT_WINDOW - timedelta(seconds=age))
    with patch('django.utils.timezone.now', return_value=now):
        chart = build_tape_chart(hotel_a)
    assert chart['move_data']['stays'][str(stay.pk)]['canMove'] is allowed
    assert chart['rows'][0]['bars'][0]['can_move'] is allowed


def test_clipped_booking_checks_collisions_beyond_window_and_keeps_expired_holds(hotel_a):
    today = timezone.localdate()
    booking = BookingFactory(property=hotel_a, check_in_date=today, check_out_date=today + timedelta(days=30))
    first = BookingRoomFactory(booking=booking)
    target = RoomFactory(property=hotel_a, room_type=first.room_type, hk_status='DIRTY')
    hold = BookingFactory(property=hotel_a, status='PENDING_PAYMENT',
                          check_in_date=today + timedelta(days=20), check_out_date=today + timedelta(days=22))
    Booking.objects.filter(pk=hold.pk).update(created_at=timezone.now() - PAYMENT_WINDOW)
    hidden = BookingRoomFactory(booking=hold, room=target)
    chart = build_tape_chart(hotel_a)
    assert str(hidden.pk) not in chart['move_data']['stays']
    assert chart['move_data']['rooms'][str(target.pk)]['stays'] == [{
        'id': str(hidden.pk), 'start': hidden.stay_range.lower.isoformat(), 'end': hidden.stay_range.upper.isoformat(),
    }]
    assert chart['move_data']['stays'][str(first.pk)]['end'] == booking.check_out_date.isoformat()


def test_filter_and_property_isolation_for_move_metadata(client, hotel_a, hotel_b, login_member):
    login_member(hotel_a)
    mine = BookingRoomFactory(booking=BookingFactory(property=hotel_a), room=RoomFactory(property=hotel_a, floor=1))
    excluded = BookingRoomFactory(booking=BookingFactory(property=hotel_a), room=RoomFactory(property=hotel_a, floor=2))
    other = BookingRoomFactory(booking=BookingFactory(property=hotel_b))
    before = other.room_id
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url(hotel_a), {'floor': 1, 'property_id': str(hotel_b.pk)}, HTTP_HX_REQUEST='true')
    data = payload(response)
    assert set(data['rooms']) == {str(mine.room_id)}
    assert set(data['stays']) == {str(mine.pk)}
    assert str(other.pk) not in data['stays'] and str(excluded.pk) not in data['stays']
    assert not any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries)
    other.refresh_from_db()
    assert other.room_id == before


def test_metadata_json_escapes_room_text(client, hotel_a, login_member):
    login_member(hotel_a)
    RoomFactory(property=hotel_a, number='</script><b>x</b>')
    response = client.get(url(hotel_a))
    assert '</script><b>x</b>' not in response.content.decode()
    assert next(iter(payload(response)['rooms'].values()))['number'] == '</script><b>x</b>'


def test_inactive_and_released_rooms(hotel_a):
    booking = BookingFactory(property=hotel_a)
    stay = BookingRoomFactory(booking=booking, room=RoomFactory(property=hotel_a, is_active=False))
    released = BookingRoomFactory(booking=booking, is_active=False)
    chart = build_tape_chart(hotel_a)
    assert chart['move_data']['rooms'][str(stay.room_id)]['active'] is False
    assert str(released.pk) not in chart['move_data']['stays']
    assert all(entry['id'] != str(released.pk) for room in chart['move_data']['rooms'].values() for entry in room['stays'])


@pytest.mark.parametrize('method', ['get', 'post', 'patch', 'delete'])
def test_foreign_property_cannot_get_or_write_move_data(client, hotel_a, hotel_b, login_member, method):
    login_member(hotel_a)
    stay = BookingRoomFactory(booking=BookingFactory(property=hotel_b))
    with CaptureQueriesContext(connection) as queries:
        response = getattr(client, method)(url(hotel_b), {'booking_room_id': stay.pk})
    assert response.status_code == 404
    assert not any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries)


def test_housekeeping_cannot_read_move_payload(client, hotel_a, login_member):
    login_member(hotel_a, Membership.Role.HOUSEKEEPING)
    assert client.get(url(hotel_a), HTTP_HX_REQUEST='true').status_code == 403
