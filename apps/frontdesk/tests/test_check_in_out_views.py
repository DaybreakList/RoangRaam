from datetime import timedelta

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.bookings.folio import close_bill
from apps.bookings.models import Booking
from apps.bookings.tests.factories import BookingFactory, BookingRoomFactory
from apps.frontdesk.services.daily_ops import property_today
from apps.properties.models import Membership
from apps.rooms.models import Room
from apps.rooms.tests.factories import RoomFactory

pytestmark = pytest.mark.django_db

Role = Membership.Role
ID = {'id_type': 'PASSPORT', 'id_number': 'AA1234567', 'nationality': 'Japan'}


def arriving_today(hotel, status=Booking.Status.CONFIRMED, days_ago=0):
    check_in_date = property_today(hotel) - timedelta(days=days_ago)
    booking = BookingFactory(
        property=hotel, status=status,
        check_in_date=check_in_date, check_out_date=check_in_date + timedelta(days=2),
    )
    BookingRoomFactory(booking=booking, room=RoomFactory(property=hotel, hk_status=Room.HousekeepingStatus.CLEAN))
    return booking


def url(name, booking):
    return reverse(f'check_in_out:{name}', kwargs={'property_slug': booking.property.slug, 'booking_id': booking.pk})


def today_url(hotel):
    return reverse('daily_ops:index', kwargs={'property_slug': hotel.slug})


def texts(response):
    return [str(message) for message in get_messages(response.wsgi_request)]


@pytest.mark.parametrize('role', [Role.OWNER, Role.MANAGER, Role.FRONT_DESK])
def test_front_desk_roles_check_in_and_go_back_to_today(client, hotel_a, login_member, role):
    booking = arriving_today(hotel_a)
    login_member(hotel_a, role=role)

    response = client.post(url('check_in', booking), ID)

    assert response.status_code == 302
    assert response['Location'] == today_url(hotel_a)
    assert texts(response) == [f'เช็คอินการจอง {booking.code} เรียบร้อยแล้ว']
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CHECKED_IN


def test_check_in_with_a_room_change(client, hotel_a, login_member):
    booking = arriving_today(hotel_a)
    booking_room = booking.booking_rooms.get()
    other = RoomFactory(property=hotel_a, room_type=booking_room.room_type, hk_status=Room.HousekeepingStatus.CLEAN)
    login_member(hotel_a)

    client.post(url('check_in', booking), {**ID, f'room_{booking_room.pk}': other.pk})

    booking_room.refresh_from_db()
    assert booking_room.room == other


def test_check_out_marks_the_room_dirty(client, hotel_a, login_member):
    booking = arriving_today(hotel_a, status=Booking.Status.CHECKED_IN, days_ago=1)
    close_bill(hotel_a, booking.pk, user=None)
    login_member(hotel_a)

    response = client.post(url('check_out', booking))

    assert response['Location'] == today_url(hotel_a)
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CHECKED_OUT
    assert booking.booking_rooms.get().room.hk_status == Room.HousekeepingStatus.DIRTY


def test_check_out_with_an_open_bill_shows_why(client, hotel_a, login_member):
    booking = arriving_today(hotel_a, status=Booking.Status.CHECKED_IN, days_ago=1)
    login_member(hotel_a)

    response = client.post(url('check_out', booking))

    assert response['Location'] == today_url(hotel_a)
    assert texts(response) == ['กรุณารับชำระและปิดบิลก่อนเช็คเอาต์']
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CHECKED_IN


@pytest.mark.parametrize('name', ['check_in', 'check_out'])
def test_housekeeping_is_refused(client, hotel_a, login_member, name):
    booking = arriving_today(hotel_a)
    login_member(hotel_a, role=Role.HOUSEKEEPING)

    assert client.post(url(name, booking), ID).status_code == 403
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED


@pytest.mark.parametrize('name', ['check_in', 'check_out'])
def test_staff_of_another_hotel_get_404_for_every_method(client, hotel_a, hotel_b, login_member,
                                                         assert_404_all_methods, name):
    booking = arriving_today(hotel_a)
    login_member(hotel_b, role=Role.OWNER)

    assert_404_all_methods(url(name, booking))
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED


@pytest.mark.parametrize('name', ['check_in', 'check_out'])
def test_a_booking_of_another_hotel_under_this_hotels_url_is_404(client, hotel_a, hotel_b, login_member, name):
    other = arriving_today(hotel_b)
    login_member(hotel_a, role=Role.OWNER)
    foreign = reverse(f'check_in_out:{name}', kwargs={'property_slug': hotel_a.slug, 'booking_id': other.pk})

    assert client.post(foreign, ID).status_code == 404
    other.refresh_from_db()
    assert other.status == Booking.Status.CONFIRMED


def test_a_room_of_another_hotel_in_the_body_is_404(client, hotel_a, hotel_b, login_member):
    booking = arriving_today(hotel_a)
    booking_room = booking.booking_rooms.get()
    foreign_room = RoomFactory(property=hotel_b, hk_status=Room.HousekeepingStatus.CLEAN)
    login_member(hotel_a)

    response = client.post(url('check_in', booking), {**ID, f'room_{booking_room.pk}': foreign_room.pk})

    assert response.status_code == 404
    booking.refresh_from_db()
    booking_room.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED
    assert booking_room.room_id != foreign_room.pk


@pytest.mark.parametrize('name', ['check_in', 'check_out'])
def test_get_is_not_allowed(client, hotel_a, login_member, name):
    login_member(hotel_a)

    assert client.get(url(name, arriving_today(hotel_a))).status_code == 405


def test_anonymous_visitors_are_sent_to_login(client, hotel_a):
    response = client.post(url('check_in', arriving_today(hotel_a)), ID)

    assert response.status_code == 302
    assert response['Location'].startswith('/accounts/login/')


@pytest.mark.parametrize('data', [
    {'id_type': 'PASSPORT'},
    {'id_type': 'DRIVING_LICENCE', 'id_number': 'X1'},
    {**ID, 'room_abc': '1'},
])
def test_bad_form_data_is_rejected_with_a_message(client, hotel_a, login_member, data):
    booking = arriving_today(hotel_a)
    login_member(hotel_a)

    response = client.post(url('check_in', booking), data)

    assert response['Location'] == today_url(hotel_a)
    assert texts(response)
    booking.refresh_from_db()
    assert booking.status == Booking.Status.CONFIRMED


def test_a_second_check_in_post_changes_nothing(client, hotel_a, login_member):
    booking = arriving_today(hotel_a)
    login_member(hotel_a)
    client.post(url('check_in', booking), ID)
    first_time = booking.booking_rooms.get().checked_in_at

    response = client.post(url('check_in', booking), ID)

    # The Today page does not show messages yet, so the first success message is still queued
    assert texts(response)[-1] == 'เช็คอินไม่ได้ เพราะการจองมีสถานะ "เช็คอินแล้ว"'
    assert booking.booking_rooms.get().checked_in_at == first_time
