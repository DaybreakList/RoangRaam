import pytest
from django.db import connection
from django.http import Http404
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.frontdesk.services.room_status import mark_room_clean, room_status_board
from apps.properties.models import Membership
from apps.rooms.models import Room
from apps.rooms.tests.factories import RoomFactory, RoomTypeFactory

pytestmark = pytest.mark.django_db

Role = Membership.Role
CLEAN = Room.HousekeepingStatus.CLEAN
DIRTY = Room.HousekeepingStatus.DIRTY


def board_url(hotel, **query):
    url = reverse('room_status:index', kwargs={'property_slug': hotel.slug})
    return f'{url}?' + '&'.join(f'{key}={value}' for key, value in query.items()) if query else url


def clean_url(room):
    return reverse('room_status:mark_clean', kwargs={'property_slug': room.property.slug, 'room_id': room.pk})


@pytest.fixture
def rooms(hotel_a):
    """Floor 1: 101 Clean, 102 Dirty; floor 2: 201 Dirty; one closed Dirty room."""
    room_type = RoomTypeFactory(property=hotel_a, name='Deluxe')
    return {
        '101': RoomFactory(property=hotel_a, room_type=room_type, number='101', floor=1, hk_status=CLEAN),
        '102': RoomFactory(property=hotel_a, room_type=room_type, number='102', floor=1, hk_status=DIRTY),
        '201': RoomFactory(property=hotel_a, room_type=room_type, number='201', floor=2, hk_status=DIRTY),
        'closed': RoomFactory(property=hotel_a, room_type=room_type, number='999', floor=9,
                              hk_status=DIRTY, is_active=False),
    }


# ---------- Service ----------

def test_board_lists_active_rooms_dirty_first(hotel_a, rooms):
    board = room_status_board(hotel_a)

    assert [room.number for room in board.rooms] == ['102', '201', '101']
    assert (board.dirty_count, board.clean_count) == (2, 1)


def test_board_filters_by_floor_and_status(hotel_a, rooms):
    assert [room.number for room in room_status_board(hotel_a, floor=1).rooms] == ['102', '101']
    assert [room.number for room in room_status_board(hotel_a, status=CLEAN).rooms] == ['101']


def test_board_never_lists_another_hotels_rooms(hotel_a, hotel_b, rooms):
    RoomFactory(property=hotel_b, number='B-1', hk_status=DIRTY)

    assert 'B-1' not in [room.number for room in room_status_board(hotel_a).rooms]


def test_board_runs_one_query_however_many_rooms(hotel_a, rooms):
    with CaptureQueriesContext(connection) as few:
        room_status_board(hotel_a)
    for number in range(20):
        RoomFactory(property=hotel_a, number=f'3{number:02d}', floor=3)
    with CaptureQueriesContext(connection) as many:
        board = room_status_board(hotel_a)
        [room.room_type.name for room in board.rooms]

    assert len(few) == len(many) == 1


def test_mark_clean_changes_a_dirty_room(hotel_a, rooms):
    room, changed = mark_room_clean(hotel_a, rooms['102'].pk)

    assert changed is True
    rooms['102'].refresh_from_db()
    assert rooms['102'].hk_status == CLEAN


def test_mark_clean_twice_is_harmless(hotel_a, rooms):
    mark_room_clean(hotel_a, rooms['102'].pk)

    room, changed = mark_room_clean(hotel_a, rooms['102'].pk)

    assert changed is False
    assert room.hk_status == CLEAN


def test_mark_clean_of_another_hotels_room_is_404(hotel_a, hotel_b):
    other = RoomFactory(property=hotel_b, hk_status=DIRTY)

    with pytest.raises(Http404):
        mark_room_clean(hotel_a, other.pk)

    other.refresh_from_db()
    assert other.hk_status == DIRTY


# ---------- Board page ----------

@pytest.mark.parametrize('role', [Role.OWNER, Role.MANAGER, Role.FRONT_DESK, Role.HOUSEKEEPING])
def test_every_role_sees_the_board(client, hotel_a, rooms, login_member, role):
    login_member(hotel_a, role=role)

    response = client.get(board_url(hotel_a))

    assert response.status_code == 200
    assert [room.number for room in response.context['board'].rooms] == ['102', '201', '101']


@pytest.mark.parametrize('role, sees_button', [
    (Role.HOUSEKEEPING, True), (Role.MANAGER, True), (Role.OWNER, True), (Role.FRONT_DESK, False),
])
def test_only_cleaning_roles_see_the_button(client, hotel_a, rooms, login_member, role, sees_button):
    login_member(hotel_a, role=role)

    content = client.get(board_url(hotel_a)).content.decode()

    assert (clean_url(rooms['102']) in content) is sees_button
    assert clean_url(rooms['101']) not in content  # already Clean


def test_the_board_filters_from_the_query_string(client, hotel_a, rooms, login_member):
    login_member(hotel_a, role=Role.HOUSEKEEPING)

    response = client.get(board_url(hotel_a, floor=2, status=DIRTY))

    assert [room.number for room in response.context['board'].rooms] == ['201']


@pytest.mark.parametrize('query', [{'floor': 'abc'}, {'floor': 42}, {'status': 'SPARKLING'}])
def test_bad_filters_show_an_error_and_every_room(client, hotel_a, rooms, login_member, query):
    login_member(hotel_a, role=Role.HOUSEKEEPING)

    response = client.get(board_url(hotel_a, **query))

    assert response.status_code == 200
    assert 'rr-field__errors' in response.content.decode()
    assert len(response.context['board'].rooms) == 3


def test_staff_of_another_hotel_get_404_on_the_board(client, hotel_a, hotel_b, rooms, login_member,
                                                     assert_404_all_methods):
    login_member(hotel_b, role=Role.OWNER)

    assert_404_all_methods(board_url(hotel_a))


def test_the_board_only_reads(client, hotel_a, login_member):
    login_member(hotel_a, role=Role.OWNER)

    assert client.post(board_url(hotel_a)).status_code == 405


def test_anonymous_visitors_are_sent_to_login(client, hotel_a):
    response = client.get(board_url(hotel_a))

    assert response.status_code == 302
    assert response['Location'].startswith('/accounts/login/')


# ---------- Mark clean ----------

@pytest.mark.parametrize('role', [Role.HOUSEKEEPING, Role.MANAGER, Role.OWNER])
def test_cleaning_roles_mark_a_room_clean(client, hotel_a, rooms, login_member, role):
    login_member(hotel_a, role=role)

    response = client.post(clean_url(rooms['102']))

    assert response.status_code == 302
    assert response['Location'] == board_url(hotel_a)
    rooms['102'].refresh_from_db()
    assert rooms['102'].hk_status == CLEAN
    follow = client.get(response['Location'])
    assert 'ห้อง 102 พร้อมให้เข้าพักแล้ว' in follow.content.decode()


def test_the_front_desk_cannot_mark_clean(client, hotel_a, rooms, login_member):
    login_member(hotel_a, role=Role.FRONT_DESK)

    assert client.post(clean_url(rooms['102'])).status_code == 403
    rooms['102'].refresh_from_db()
    assert rooms['102'].hk_status == DIRTY


def test_the_filters_are_kept_after_marking_clean(client, hotel_a, rooms, login_member):
    login_member(hotel_a, role=Role.HOUSEKEEPING)

    response = client.post(clean_url(rooms['201']), {'floor': '2', 'status': DIRTY})

    assert response['Location'] == board_url(hotel_a, floor=2, status=DIRTY)


def test_bad_filters_are_dropped_from_the_redirect(client, hotel_a, rooms, login_member):
    login_member(hotel_a, role=Role.HOUSEKEEPING)

    response = client.post(clean_url(rooms['201']), {'floor': 'https://evil.example', 'status': 'X'})

    assert response['Location'] == board_url(hotel_a)


def test_marking_a_clean_room_again_says_so(client, hotel_a, rooms, login_member):
    login_member(hotel_a, role=Role.HOUSEKEEPING)

    response = client.post(clean_url(rooms['101']), follow=True)

    assert 'ห้อง 101 สะอาดอยู่แล้ว' in response.content.decode()


def test_staff_of_another_hotel_get_404_on_mark_clean(client, hotel_a, hotel_b, rooms, login_member,
                                                      assert_404_all_methods):
    login_member(hotel_b, role=Role.OWNER)

    assert_404_all_methods(clean_url(rooms['102']))
    rooms['102'].refresh_from_db()
    assert rooms['102'].hk_status == DIRTY


def test_a_room_of_another_hotel_under_this_hotels_url_is_404(client, hotel_a, hotel_b, login_member):
    other = RoomFactory(property=hotel_b, hk_status=DIRTY)
    login_member(hotel_a, role=Role.HOUSEKEEPING)
    foreign = reverse('room_status:mark_clean', kwargs={'property_slug': hotel_a.slug, 'room_id': other.pk})

    assert client.post(foreign).status_code == 404
    other.refresh_from_db()
    assert other.hk_status == DIRTY


def test_get_on_mark_clean_is_not_allowed(client, hotel_a, rooms, login_member):
    login_member(hotel_a, role=Role.HOUSEKEEPING)

    assert client.get(clean_url(rooms['102'])).status_code == 405
