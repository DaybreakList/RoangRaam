from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.frontdesk.tape_chart_forms import LAST_START
from apps.frontdesk.tests.test_tape_chart import stay, url
from apps.properties.models import Membership
from apps.rooms.tests.factories import RoomFactory, RoomTypeFactory

pytestmark = pytest.mark.django_db


def ids(response):
    return [row['room'].pk for row in response.context['chart']['rows']]


@pytest.mark.parametrize('mode', ['floor', 'room_type', 'both'])
def test_filters(client, hotel_a, login_member, mode):
    login_member(hotel_a)
    twin = RoomTypeFactory(property=hotel_a)
    one = RoomFactory(property=hotel_a, room_type=twin, floor=1)
    two = RoomFactory(property=hotel_a, room_type=twin, floor=2)
    three = RoomFactory(property=hotel_a, floor=2)
    params = {'floor': '2'} if mode == 'floor' else {'room_type': twin.pk}
    expected = [two.pk, three.pk] if mode == 'floor' else [one.pk, two.pk]
    if mode == 'both':
        params['floor'] = '2'
        expected = [two.pk]
    assert ids(client.get(url(hotel_a), params)) == expected


def test_floor_zero_and_negative(client, hotel_a, login_member):
    login_member(hotel_a)
    for floor in [-1, 0, 1]:
        room = RoomFactory(property=hotel_a, floor=floor)
        assert ids(client.get(url(hotel_a), {'floor': floor})) == [room.pk]


def test_empty_intersection(client, hotel_a, login_member):
    login_member(hotel_a)
    room = RoomFactory(property=hotel_a, floor=1)
    RoomFactory(property=hotel_a, floor=2)
    response = client.get(url(hotel_a), {'floor': 2, 'room_type': room.room_type_id})
    assert ids(response) == []
    assert 'ยังไม่มีห้องที่ตรงกับเงื่อนไข' in response.content.decode()


@pytest.mark.parametrize('partial', [False, True])
def test_foreign_room_type_404(client, hotel_a, hotel_b, login_member, partial):
    login_member(hotel_a)
    other = RoomTypeFactory(property=hotel_b)
    response = client.get(url(hotel_a), {'room_type': other.pk}, HTTP_HX_REQUEST='true' if partial else 'false')
    assert response.status_code == 404


def test_missing_room_type_404(client, hotel_a, login_member):
    login_member(hotel_a)
    assert client.get(url(hotel_a), {'room_type': 999999}).status_code == 404


def test_choices_and_partial_never_leak_foreign_names(client, hotel_a, hotel_b, login_member):
    login_member(hotel_a)
    mine = stay(hotel_a)
    other = stay(hotel_b)
    other.room.floor = 999
    other.room.save()
    response = client.get(url(hotel_a), HTTP_HX_REQUEST='true')
    content = response.content.decode()
    assert mine.booking.code in content
    for secret in [other.booking.code, str(other.booking.guest), other.room_type.name]:
        assert secret not in content
    assert '999' not in [str(value) for value, label in response.context['form'].fields['floor'].choices]


@pytest.mark.parametrize('params,field', [
    ({'start': 'nonsense'}, 'start'), ({'start': '2026-02-30'}, 'start'),
    ({'start': ''}, 'start'), ({'start': '9999-12-31'}, 'start'),
    ({'floor': 'abc'}, 'floor'), ({'floor': '999'}, 'floor'),
    ({'room_type': 'abc'}, 'room_type'),
])
def test_invalid_filters_render_errors(client, hotel_a, login_member, params, field):
    login_member(hotel_a)
    response = client.get(url(hotel_a), params, HTTP_HX_REQUEST='true')
    assert response.status_code == 200
    assert field in response.context['form'].errors
    assert response.context['chart'] is None
    assert 'กรุณาตรวจสอบ' in response.content.decode()


def test_select_past_range_and_clip(client, hotel_a, login_member):
    login_member(hotel_a)
    item = stay(hotel_a, -10, -5)
    start = timezone.localdate() - timedelta(days=8)
    response = client.get(url(hotel_a), {'start': start.isoformat()})
    bar = response.context['chart']['rows'][0]['bars'][0]
    assert bar['code'] == item.booking.code
    assert (bar['column'], bar['span'], bar['continues_before']) == (1, 3, True)


@pytest.mark.parametrize('start,previous,next_', [('0001-01-01', False, True), (LAST_START.isoformat(), True, False)])
def test_extreme_navigation_does_not_overflow(client, hotel_a, login_member, start, previous, next_):
    login_member(hotel_a)
    response = client.get(url(hotel_a), {'start': start})
    assert response.status_code == 200
    assert bool(response.context['previous_url']) == previous
    assert bool(response.context['next_url']) == next_


def test_navigation_preserves_filters(client, hotel_a, login_member):
    login_member(hotel_a)
    room = RoomFactory(property=hotel_a, floor=0)
    start = date(2026, 12, 25)
    response = client.get(url(hotel_a), {'start': start.isoformat(), 'floor': 0, 'room_type': room.room_type_id})
    for key, target in [('previous_url', start-timedelta(days=14)), ('next_url', start+timedelta(days=14)), ('today_url', timezone.localdate())]:
        params = parse_qs(urlsplit(response.context[key]).query)
        assert params == {'start': [target.isoformat()], 'floor': ['0'], 'room_type': [str(room.room_type_id)]}


def test_htmx_partial_and_history_restore(client, hotel_a, login_member):
    login_member(hotel_a)
    params = {'start': '2026-11-01'}
    full = client.get(url(hotel_a), params)
    partial = client.get(url(hotel_a), params, HTTP_HX_REQUEST='true')
    restored = client.get(url(hotel_a), params, HTTP_HX_REQUEST='true', HTTP_HX_HISTORY_RESTORE_REQUEST='true')
    assert '<html' in full.content.decode() and '<html' in restored.content.decode()
    assert '<html' not in partial.content.decode()
    assert 'id="tc-panel"' in partial.content.decode()
    assert 'HX-Request' in partial.headers['Vary']
    assert 'HX-History-Restore-Request' in partial.headers['Vary']


def test_closed_room_visible_with_filter(client, hotel_a, login_member):
    login_member(hotel_a)
    room = RoomFactory(property=hotel_a, floor=3, is_active=False)
    stay(hotel_a, room=room)
    assert ids(client.get(url(hotel_a), {'floor': 3, 'room_type': room.room_type_id})) == [room.pk]


def test_filtered_render_query_count_is_constant(client, hotel_a, login_member):
    login_member(hotel_a)
    room = RoomFactory(property=hotel_a, floor=1)
    stay(hotel_a, room=room)
    with CaptureQueriesContext(connection) as small:
        assert client.get(url(hotel_a), {'floor': 1}).status_code == 200
    for _ in range(5):
        stay(hotel_a, room=RoomFactory(property=hotel_a, floor=1))
    with CaptureQueriesContext(connection) as large:
        assert client.get(url(hotel_a), {'floor': 1}).status_code == 200
    assert len(small) == len(large)


def test_htmx_denies_housekeeping(client, hotel_a, login_member):
    login_member(hotel_a, Membership.Role.HOUSEKEEPING)
    assert client.get(url(hotel_a), HTTP_HX_REQUEST='true').status_code == 403
