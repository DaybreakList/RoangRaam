from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.bookings.cancellation import cancel_booking
from apps.bookings.folio import close_bill
from apps.bookings.models import Booking
from apps.bookings.tests.factories import BookingFactory, BookingRoomFactory
from apps.frontdesk.services.check_in_out import check_out
from apps.frontdesk.services.room_status import mark_room_clean
from apps.frontdesk.services.tape_chart import build_tape_chart
from apps.properties.models import Membership
from apps.rooms.models import Room
from apps.rooms.tests.factories import RoomFactory

pytestmark = pytest.mark.django_db


def url(prop):
    return reverse('tape_chart:index', kwargs={'property_slug': prop.slug})


def stay(prop, start=1, end=3, **kwargs):
    today = timezone.localdate()
    booking = BookingFactory(property=prop, check_in_date=today + timedelta(days=start),
                             check_out_date=today + timedelta(days=end))
    return BookingRoomFactory(booking=booking, **kwargs)


@pytest.mark.parametrize('role', [Membership.Role.OWNER, Membership.Role.MANAGER, Membership.Role.FRONT_DESK])
def test_allowed_roles(client, hotel_a, login_member, role):
    login_member(hotel_a, role)
    item = stay(hotel_a)
    response = client.get(url(hotel_a))
    assert response.status_code == 200
    assert item.booking.code in response.content.decode()
    assert len(response.context['chart']['days']) == 14
    assert item.booking.guest.email not in response.content.decode()


def test_housekeeping_denied(client, hotel_a, login_member):
    login_member(hotel_a, Membership.Role.HOUSEKEEPING)
    assert client.get(url(hotel_a)).status_code == 403


def test_anonymous_redirects(client, hotel_a):
    response = client.get(url(hotel_a))
    assert response.status_code == 302
    assert '/accounts/login/' in response.url


@pytest.mark.parametrize('method', ['get', 'post', 'patch', 'delete'])
def test_foreign_property_is_404_without_writes(client, hotel_a, hotel_b, login_member, method):
    login_member(hotel_a)
    with CaptureQueriesContext(connection) as queries:
        response = getattr(client, method)(url(hotel_b))
    assert response.status_code == 404
    assert not any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries)


@pytest.mark.parametrize('method', ['post', 'patch', 'delete'])
def test_read_only(client, hotel_a, login_member, method):
    login_member(hotel_a)
    assert getattr(client, method)(url(hotel_a)).status_code == 405


def test_empty_chart(client, hotel_a, login_member):
    login_member(hotel_a)
    assert 'ยังไม่มีห้อง' in client.get(url(hotel_a)).content.decode()


def test_other_property_and_query_override_do_not_leak(client, hotel_a, hotel_b, login_member):
    login_member(hotel_a)
    mine, other = stay(hotel_a), stay(hotel_b)
    response = client.get(url(hotel_a), {'property_id': str(hotel_b.pk)})
    content = response.content.decode()
    assert mine.booking.code in content
    assert other.booking.code not in content
    assert all(row['room'].property_id == hotel_a.pk for row in response.context['chart']['rows'])


@pytest.mark.parametrize('start,end,column,span,before,after', [
    (-3, 2, 1, 2, True, False), (12, 17, 13, 2, False, True),
    (-2, 17, 1, 14, True, True), (0, 1, 1, 1, False, False),
])
def test_clips_range(hotel_a, start, end, column, span, before, after):
    stay(hotel_a, start, end)
    bar = build_tape_chart(hotel_a)['rows'][0]['bars'][0]
    assert (bar['column'], bar['span'], bar['continues_before'], bar['continues_after']) == (column, span, before, after)


def test_checkout_boundary_and_adjacent_bookings(hotel_a):
    room = RoomFactory(property=hotel_a)
    stay(hotel_a, -2, 0, room=room)
    first = stay(hotel_a, 0, 2, room=room)
    second = stay(hotel_a, 2, 4, room=room)
    stay(hotel_a, 14, 16, room=room)
    bars = build_tape_chart(hotel_a)['rows'][0]['bars']
    assert [bar['code'] for bar in bars] == [first.booking.code, second.booking.code]
    assert [(bar['column'], bar['span']) for bar in bars] == [(1, 2), (3, 2)]


def test_multi_room_booking(hotel_a):
    first = stay(hotel_a)
    BookingRoomFactory(booking=first.booking)
    rows = build_tape_chart(hotel_a)['rows']
    assert len(rows) == 2
    assert all(row['bars'][0]['code'] == first.booking.code for row in rows)


@pytest.mark.parametrize('status', ['PENDING_PAYMENT', 'CONFIRMED', 'CHECKED_IN', 'CHECKED_OUT'])
def test_status_labels_and_history(client, hotel_a, login_member, status):
    login_member(hotel_a)
    item = stay(hotel_a)
    item.booking.status = status
    item.booking.save(update_fields=['status'])
    content = client.get(url(hotel_a)).content.decode()
    assert item.booking.get_status_display() in content
    assert 'tc-' + status.lower() in content


def test_cancellation_removes_bar(hotel_a):
    item = stay(hotel_a)
    cancel_booking(hotel_a, item.booking_id)
    assert build_tape_chart(hotel_a)['rows'][0]['bars'] == []


def test_closed_rooms_with_stays_remain_visible(client, hotel_a, login_member):
    login_member(hotel_a)
    occupied = RoomFactory(property=hotel_a, is_active=False)
    empty = RoomFactory(property=hotel_a, is_active=False)
    item = stay(hotel_a, room=occupied)
    response = client.get(url(hotel_a))
    assert [row['room'].pk for row in response.context['chart']['rows']] == [occupied.pk]
    assert empty.pk != occupied.pk
    assert 'ปิดใช้งาน' in response.content.decode()
    assert item.booking.code in response.content.decode()


def test_query_count_is_constant(hotel_a):
    stay(hotel_a)
    with CaptureQueriesContext(connection) as small:
        build_tape_chart(hotel_a)
    for _ in range(5):
        stay(hotel_a)
    with CaptureQueriesContext(connection) as large:
        build_tape_chart(hotel_a)
    assert len(small) == len(large) == 2


def test_property_timezone(hotel_a):
    hotel_a.timezone = 'America/Los_Angeles'
    instant = datetime(2026, 10, 19, 1, tzinfo=dt_timezone.utc)
    with patch('django.utils.timezone.now', return_value=instant):
        assert build_tape_chart(hotel_a)['start'].isoformat() == '2026-10-18'


def test_booking_text_is_escaped(client, hotel_a, login_member):
    login_member(hotel_a)
    item = stay(hotel_a)
    Booking.objects.filter(pk=item.booking_id).update(code='<b>x</b>')
    content = client.get(url(hotel_a)).content.decode()
    assert '&lt;b&gt;x&lt;/b&gt;' in content
    assert '<b>x</b>' not in content


def test_guest_name_shown_without_contact_details(client, hotel_a, login_member):
    login_member(hotel_a)
    item = stay(hotel_a)
    content = client.get(url(hotel_a)).content.decode()
    assert str(item.booking.guest) in content
    assert item.booking.guest.email not in content
    assert item.booking.guest.phone not in content


def test_guest_name_is_escaped(client, hotel_a, login_member):
    login_member(hotel_a)
    item = stay(hotel_a)
    guest = item.booking.guest
    guest.first_name = '<script>alert(1)</script>'
    guest.save(update_fields=['first_name'])
    content = client.get(url(hotel_a)).content.decode()
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in content
    assert '<script>alert(1)</script>' not in content


def test_guest_names_do_not_cross_properties(client, hotel_a, hotel_b, login_member):
    login_member(hotel_a)
    mine, other = stay(hotel_a), stay(hotel_b)
    content = client.get(url(hotel_a)).content.decode()
    assert str(mine.booking.guest) in content
    assert str(other.booking.guest) not in content


def test_inconsistent_foreign_guest_is_not_exposed(client, hotel_a, hotel_b, login_member):
    login_member(hotel_a)
    mine, other = stay(hotel_a), stay(hotel_b)
    # Protect reads even if a non-validating writer introduced a bad relation.
    Booking.objects.filter(pk=mine.booking_id).update(guest=other.booking.guest)
    content = client.get(url(hotel_a)).content.decode()
    assert str(other.booking.guest) not in content
    assert mine.booking.code not in content


@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('status,label', [('CLEAN', 'สะอาด'), ('DIRTY', 'รอทำความสะอาด')])
def test_room_cleanliness_without_booking(client, hotel_a, login_member, partial, status, label):
    login_member(hotel_a)
    RoomFactory(property=hotel_a, hk_status=status)
    headers = {'HTTP_HX_REQUEST': 'true'} if partial else {}
    response = client.get(url(hotel_a), **headers)
    assert response.status_code == 200
    content = response.content.decode()
    assert f'class="tc-housekeeping tc-housekeeping--{status.lower()}">{label}</span>' in content
    assert 'ไม่มีการจองในช่วงนี้' in content


def test_closed_room_keeps_cleanliness_and_booking_status(client, hotel_a, login_member):
    login_member(hotel_a)
    room = RoomFactory(property=hotel_a, is_active=False, hk_status=Room.HousekeepingStatus.DIRTY)
    item = stay(hotel_a, room=room)
    content = client.get(url(hotel_a)).content.decode()
    assert 'tc-housekeeping--dirty">รอทำความสะอาด</span>' in content
    assert 'ปิดใช้งาน' in content
    assert 'tc-bar tc-confirmed' in content
    assert item.booking.code in content


@pytest.mark.parametrize('partial', [False, True])
def test_cleanliness_refreshes_after_checkout_and_cleaning(client, hotel_a, login_member, partial):
    login_member(hotel_a)
    room = RoomFactory(property=hotel_a, hk_status=Room.HousekeepingStatus.CLEAN)
    item = stay(hotel_a, -1, 2, room=room)
    item.booking.status = Booking.Status.CHECKED_IN
    item.booking.save(update_fields=['status'])
    headers = {'HTTP_HX_REQUEST': 'true'} if partial else {}
    assert 'tc-housekeeping--clean' in client.get(url(hotel_a), **headers).content.decode()
    close_bill(hotel_a, item.booking_id, user=None)  # check_out() needs a closed bill
    check_out(hotel_a, item.booking_id, today=item.booking.check_in_date + timedelta(days=1))
    content = client.get(url(hotel_a), **headers).content.decode()
    assert 'tc-housekeeping--dirty">รอทำความสะอาด</span>' in content
    assert 'tc-bar tc-checked_out' in content
    mark_room_clean(hotel_a, room.pk)
    content = client.get(url(hotel_a), **headers).content.decode()
    assert 'tc-housekeeping--clean">สะอาด</span>' in content
    assert 'tc-housekeeping--dirty' not in content
    assert 'tc-bar tc-checked_out' in content


@pytest.mark.parametrize('partial', [False, True])
def test_foreign_room_cleanliness_is_not_exposed(client, hotel_a, hotel_b, login_member, partial):
    login_member(hotel_a)
    RoomFactory(property=hotel_a, number='LOCAL-CLEAN', hk_status=Room.HousekeepingStatus.CLEAN)
    other = RoomFactory(property=hotel_b, number='FOREIGN-DIRTY', hk_status=Room.HousekeepingStatus.DIRTY)
    headers = {'HTTP_HX_REQUEST': 'true'} if partial else {}
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url(hotel_a), {'property_id': str(hotel_b.pk)}, **headers)
    content = response.content.decode()
    assert 'LOCAL-CLEAN' in content
    assert 'tc-housekeeping--clean' in content
    assert other.number not in content
    assert 'tc-housekeeping--dirty' not in content
    assert not any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries)
    other.refresh_from_db()
    assert other.hk_status == Room.HousekeepingStatus.DIRTY
