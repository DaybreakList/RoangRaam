from datetime import date, timedelta
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlsplit

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.bookings.models import Booking
from apps.bookings.payments import PAYMENT_WINDOW
from apps.bookings.tests.factories import BookingFactory, BookingRoomFactory
from apps.frontdesk.services.tape_chart import build_tape_chart
from apps.frontdesk.tests.test_tape_chart import stay, url
from apps.frontdesk.tests.test_tape_chart_drag import payload
from apps.properties.models import Membership
from apps.rooms.tests.factories import RoomFactory, RoomTypeFactory

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize('partial', [False, True])
@pytest.mark.parametrize('days', [14, 60])
def test_window_and_navigation(client, hotel_a, login_member, days, partial):
    login_member(hotel_a)
    room = RoomFactory(property=hotel_a, floor=0)
    start = date(2026, 11, 3)
    params = {'start': start.isoformat(), 'days': days, 'floor': 0, 'room_type': room.room_type_id}
    response = client.get(url(hotel_a), params, HTTP_HX_REQUEST='true' if partial else 'false')
    assert response.status_code == 200
    chart = response.context['chart']
    assert len(chart['days']) == chart['window_days'] == days
    assert chart['end'] == start + timedelta(days=days)
    assert f'--tc-days: {days}' in response.content.decode()
    for key, target in [('previous_url', start - timedelta(days=days)),
                        ('next_url', start + timedelta(days=days)), ('today_url', timezone.localdate())]:
        query = parse_qs(urlsplit(response.context[key]).query)
        assert query['start'] == [target.isoformat()]
        assert query.get('days', ['14']) == [str(days)]
        assert query['floor'] == ['0'] and query['room_type'] == [str(room.room_type_id)]
    reset = client.get(response.context['clear_url'])
    assert reset.context['chart']['window_days'] == days
    assert reset.context['form'].cleaned_data['floor'] is None
    assert reset.context['form'].cleaned_data['room_type'] is None


@pytest.mark.parametrize('days', ['0', '-1', '15', '61', '999999999999999999', '60.0', 'abc'])
def test_invalid_window_is_html_error(client, hotel_a, login_member, days):
    login_member(hotel_a)
    response = client.get(url(hotel_a), {'days': days}, HTTP_HX_REQUEST='true')
    assert response.status_code == 200
    assert 'days' in response.context['form'].errors
    assert response.context['chart'] is None


@pytest.mark.parametrize('start,valid,previous,next_', [
    (date.min, True, False, True), (date.max - timedelta(days=60), True, True, False),
    (date.max - timedelta(days=59), False, False, False),
])
def test_sixty_day_extremes(client, hotel_a, login_member, start, valid, previous, next_):
    login_member(hotel_a)
    response = client.get(url(hotel_a), {'start': start.isoformat(), 'days': 60})
    assert response.status_code == 200
    assert response.context['form'].is_valid() is valid
    if valid:
        assert bool(response.context['previous_url']) is previous
        assert bool(response.context['next_url']) is next_
    else:
        assert 'ครบ 60 วัน' in response.content.decode()


def test_sixty_day_clipping_and_full_stay_collision(hotel_a):
    source = stay(hotel_a, -5, 90)
    target = RoomFactory(property=hotel_a, room_type=source.room_type, hk_status='DIRTY')
    outside = stay(hotel_a, 80, 82, room=target)
    Booking.objects.filter(pk=outside.booking_id).update(status='PENDING_PAYMENT', created_at=timezone.now() - PAYMENT_WINDOW)
    boundary = stay(hotel_a, 60, 61)
    chart = build_tape_chart(hotel_a, days=60)
    bar = next(row['bars'][0] for row in chart['rows'] if row['room'].pk == source.room_id)
    assert (bar['column'], bar['span'], bar['continues_before'], bar['continues_after']) == (1, 60, True, True)
    assert bar['end'] == source.stay_range.upper
    data = chart['move_data']
    assert str(outside.pk) not in data['stays'] and str(boundary.pk) not in data['stays']
    assert data['rooms'][str(target.pk)]['stays'][0]['id'] == str(outside.pk)


@pytest.mark.parametrize('partial', [False, True])
def test_sixty_day_isolation_roles_and_read_only(client, hotel_a, hotel_b, login_member, partial):
    login_member(hotel_a)
    mine, other = stay(hotel_a, 45, 48), stay(hotel_b, 45, 48)
    params = {'days': 60, 'property_id': str(hotel_b.pk)}
    headers = {'HTTP_HX_REQUEST': 'true' if partial else 'false'}
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url(hotel_a), params, **headers)
    assert set(payload(response)['stays']) == {str(mine.pk)}
    assert other.booking.code not in response.content.decode()
    assert not any(q['sql'].lstrip().upper().startswith(('UPDATE', 'INSERT', 'DELETE')) for q in queries)
    assert client.get(url(hotel_b), params, **headers).status_code == 404
    assert client.get(url(hotel_a), {**params, 'room_type': other.room_type_id}, **headers).status_code == 404
    login_member(hotel_a, Membership.Role.HOUSEKEEPING)
    assert client.get(url(hotel_a), params, **headers).status_code == 403


@pytest.mark.parametrize('filter_by', ['floor', 'type', 'both'])
def test_filters_reduce_rows_read_from_database(hotel_a, filter_by):
    room_type = RoomTypeFactory(property=hotel_a)
    source = stay(hotel_a, 20, 25, room=RoomFactory(property=hotel_a, floor=1, room_type=room_type, is_active=False))
    stay(hotel_a, 20, 25, room=RoomFactory(property=hotel_a, floor=2, room_type=room_type))
    stay(hotel_a, 20, 25, room=RoomFactory(property=hotel_a, floor=1))
    rows_read = []

    def record(execute, sql, params, many, context):
        result = execute(sql, params, many, context)
        rows_read.append(context['cursor'].rowcount)
        return result

    kwargs = {'floor': 1} if filter_by == 'floor' else {'room_type': room_type}
    if filter_by == 'both':
        kwargs['floor'] = 1
    with connection.execute_wrapper(record):
        chart = build_tape_chart(hotel_a, days=60, **kwargs)
    expected = 1 if filter_by == 'both' else 2
    assert rows_read[0] == expected  # Prevent a return to loading every stay then filtering Python.
    assert sum(len(row['bars']) for row in chart['rows']) == expected
    assert str(source.pk) in chart['move_data']['stays']  # Closed rooms with stays remain visible.


def test_ninety_rooms_queries_and_mixed_status_markup(client, hotel_a, login_member):
    login_member(hotel_a)
    room_type = RoomTypeFactory(property=hotel_a)
    rooms = [RoomFactory(property=hotel_a, room_type=room_type, number=f'R{i:03}', hk_status='DIRTY' if i % 2 else 'CLEAN') for i in range(90)]
    first = stay(hotel_a, 20, 23, room=rooms[0])
    with CaptureQueriesContext(connection) as small:
        build_tape_chart(hotel_a, days=60)
    states = ['CONFIRMED', 'CHECKED_IN', 'CHECKED_OUT', 'PENDING_PAYMENT']
    sibling = BookingRoomFactory(booking=first.booking, room=rooms[1])
    for i, room in enumerate(rooms[2:]):
        booking = BookingFactory(property=hotel_a, status=states[i % 4],
                                 check_in_date=first.stay_range.lower, check_out_date=first.stay_range.upper)
        BookingRoomFactory(booking=booking, room=room)
    # An expired hold still occupies a room, but offers no move control.
    Booking.objects.filter(pk=first.booking_id).update(status='PENDING_PAYMENT', created_at=timezone.now() - PAYMENT_WINDOW)
    with CaptureQueriesContext(connection) as large:
        chart = build_tape_chart(hotel_a, days=60)
    assert len(small) == len(large) == 3
    assert len(chart['rows']) == 90
    assert sum(len(room['stays']) for room in chart['move_data']['rooms'].values()) == 90
    assert not chart['move_data']['stays'][str(sibling.pk)]['canMove']
    response = client.get(url(hotel_a), {'days': 60})

    class Elements(HTMLParser):
        bars = []
        handles = {}
        current = None
        rows = 0

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if 'data-room-id' in attrs:
                self.rows += 1
            if 'data-booking-room-id' in attrs:
                self.bars.append((tag, attrs))
                self.current = attrs['data-booking-room-id']
                self.handles[self.current] = []
            elif 'tc-drag-handle' in (attrs.get('class') or '').split():
                self.handles[self.current].append((tag, attrs))

        def handle_endtag(self, tag):
            if tag in ('button', 'div'):
                self.current = None

    parsed = Elements()
    parsed.feed(response.content.decode())
    assert parsed.rows == 8 and len(parsed.bars) == 8
    data = payload(response)
    assert len(data['order']) == 90 and len(data['stays']) == 90
    assert data['order'][-1] not in {str(row['room'].pk) for row in chart['initial_rows']}
    assert chart['remaining_height'] == 82 * chart['row_height']
    assert data['viewport']['rowHeight'] == chart['row_height'] == 112
    assert '--tc-row-height: 112px' in response.content.decode()
    for tag, attrs in parsed.bars:
        can_move = chart['move_data']['stays'][attrs['data-booking-room-id']]['canMove']
        assert tag == ('button' if can_move else 'div')
        assert not any(key.startswith(('@', ':', 'x-')) for key in attrs)
    assert len([bar for tag, bar in parsed.bars if tag == 'button']) > 0
    # Only movable bars carry the touch drag handle (#117); fixed bars must stay plain and pannable.
    assert {tag for tag, _ in parsed.bars} == {'button', 'div'}
    for tag, attrs in parsed.bars:
        handles = parsed.handles[attrs['data-booking-room-id']]
        if tag == 'button':
            assert [(handle_tag, handle['title'], handle['aria-hidden']) for handle_tag, handle in handles] == [
                ('span', 'ลากเพื่อย้ายห้อง', 'true')]
        else:
            assert handles == []


def test_non_displayed_hold_still_blocks_even_when_occupancy_is_reused(hotel_a, hotel_b):
    source = stay(hotel_a, 0, 3)
    target = RoomFactory(property=hotel_a, room_type=source.room_type)
    hidden = stay(hotel_a, 0, 3, room=target)
    foreign = BookingFactory(property=hotel_b)
    # A corrupt guest relation must hide guest/booking details but cannot make a held room look free.
    Booking.objects.filter(pk=hidden.booking_id).update(guest=foreign.guest)
    chart = build_tape_chart(hotel_a, days=60)
    assert str(hidden.pk) not in chart['move_data']['stays']
    assert chart['move_data']['rooms'][str(target.pk)]['stays'] == [{
        'id': str(hidden.pk), 'start': hidden.stay_range.lower.isoformat(), 'end': hidden.stay_range.upper.isoformat(),
    }]


def test_reset_invalid_filter_preserves_valid_window(client, hotel_a, login_member):
    login_member(hotel_a)
    response = client.get(url(hotel_a), {'days': 60, 'floor': 'invalid'})
    assert response.context['chart'] is None
    reset = client.get(response.context['clear_url'])
    assert reset.context['chart']['window_days'] == 60


@pytest.mark.parametrize('partial', [False, True])
def test_offscreen_render_payload_is_scoped_and_escaped(client, hotel_a, hotel_b, login_member, partial):
    login_member(hotel_a)
    rooms = [RoomFactory(property=hotel_a, number=f'R{i:03}') for i in range(12)]
    item = stay(hotel_a, 0, 3, room=rooms[-1])
    guest = item.booking.guest
    guest.first_name = '</script><img src=x onerror=alert(1)>'
    guest.save(update_fields=['first_name'])
    foreign = stay(hotel_b, 0, 3)
    response = client.get(url(hotel_a), {'days': 60}, HTTP_HX_REQUEST='true' if partial else 'false')
    content = response.content.decode()
    data = payload(response)
    assert str(item.room_id) in data['order']
    assert 'data-booking-room-id=' not in content  # The stay is outside the initial eight rows.
    assert data['stays'][str(item.pk)]['display']['guest'] == str(guest)
    assert '</script><img' not in content and foreign.booking.code not in content
    assert str(foreign.pk) not in data['stays']
    assert data['rooms'][str(item.room_id)]['bars'] == [str(item.pk)]
