import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.bookings.tests.factories import BookingFactory, BookingRoomFactory
from apps.frontdesk.services.tape_chart import build_tape_chart
from apps.frontdesk.templatetags.frontdesk_nav import can_view_page
from apps.properties.models import Membership
from apps.properties.tests.factories import MembershipFactory

pytestmark = pytest.mark.django_db

Role = Membership.Role


def tape_url(prop):
    return reverse('tape_chart:index', kwargs={'property_slug': prop.slug})


def search_url(prop):
    return reverse('bookings:search', kwargs={'property_slug': prop.slug})


def home_url(prop):
    return reverse('property_home', kwargs={'property_slug': prop.slug})


def dashboard_url(prop):
    return reverse('dashboard:hotel', kwargs={'property_slug': prop.slug})


def brand_href(response):
    return re.search(r'<a class="rr-navbar__brand" href="([^"]+)"', response.content.decode()).group(1)


def menu_hrefs(response):
    return re.findall(r'<a class="rr-navbar__link" href="([^"]+)"', response.content.decode())


def stay(prop, start=1, end=3):
    today = timezone.localdate()
    booking = BookingFactory(property=prop, check_in_date=today + timedelta(days=start),
                             check_out_date=today + timedelta(days=end))
    return BookingRoomFactory(booking=booking)


# ---------- navbar: brand link ----------

def test_guest_brand_goes_to_search_without_menu(client, hotel_a):
    response = client.get(search_url(hotel_a))
    assert response.status_code == 200
    assert brand_href(response) == search_url(hotel_a)
    assert menu_hrefs(response) == []
    assert tape_url(hotel_a) not in response.content.decode()


@pytest.mark.parametrize('role,sees_dashboard', [
    (Role.OWNER, True), (Role.MANAGER, True), (Role.FRONT_DESK, False),
])
def test_allowed_staff_see_menu_and_brand_to_home(client, hotel_a, login_member, role, sees_dashboard):
    login_member(hotel_a, role)
    response = client.get(home_url(hotel_a))
    assert response.status_code == 200
    expected = [tape_url(hotel_a)] + ([dashboard_url(hotel_a)] if sees_dashboard else [])
    assert menu_hrefs(response) == expected
    assert brand_href(response) == home_url(hotel_a)
    assert 'ตารางการจอง' in response.content.decode()


def test_housekeeping_has_no_menu(client, hotel_a, login_member):
    login_member(hotel_a, Role.HOUSEKEEPING)
    response = client.get(home_url(hotel_a))
    assert response.status_code == 200
    assert menu_hrefs(response) == []
    assert brand_href(response) == home_url(hotel_a)


def test_staff_on_public_page_of_other_hotel(client, hotel_a, hotel_b, login_member):
    login_member(hotel_a, Role.OWNER)
    response = client.get(search_url(hotel_b))
    assert response.status_code == 200
    assert menu_hrefs(response) == []
    assert tape_url(hotel_a) not in response.content.decode()
    assert tape_url(hotel_b) not in response.content.decode()
    assert brand_href(response) == search_url(hotel_b)


def test_staff_on_public_page_of_own_hotel_is_treated_as_guest(client, hotel_a, login_member):
    login_member(hotel_a, Role.OWNER)
    response = client.get(search_url(hotel_a))
    assert menu_hrefs(response) == []
    assert brand_href(response) == search_url(hotel_a)


def test_member_of_both_hotels_gets_current_hotel_link(client, hotel_a, hotel_b, login_member):
    membership = login_member(hotel_a, Role.OWNER)
    MembershipFactory(user=membership.user, property=hotel_b, role=Role.MANAGER)
    response = client.get(home_url(hotel_b))
    assert menu_hrefs(response) == [tape_url(hotel_b), dashboard_url(hotel_b)]
    assert tape_url(hotel_a) not in response.content.decode()
    assert dashboard_url(hotel_a) not in response.content.decode()
    assert brand_href(response) == home_url(hotel_b)


def test_page_without_property_has_no_menu(client, hotel_a, login_member):
    login_member(hotel_a, Role.OWNER)
    response = client.get(reverse('accounts:home'))
    assert response.status_code == 200
    assert menu_hrefs(response) == []
    assert brand_href(response) == reverse('accounts:home')


def test_menu_marks_current_page_only_on_tape_chart(client, hotel_a, login_member):
    login_member(hotel_a, Role.FRONT_DESK)
    on_home = client.get(home_url(hotel_a)).content.decode()
    on_chart = client.get(tape_url(hotel_a)).content.decode()
    assert 'class="rr-navbar__link" href="%s" aria-current="page"' % tape_url(hotel_a) in on_chart
    assert 'aria-current="page">ตารางการจอง' in on_chart
    assert 'class="rr-navbar__link" href="%s">' % tape_url(hotel_a) in on_home


# ---------- template filter ----------

@pytest.mark.parametrize('role,expected', [
    (Role.OWNER, True), (Role.MANAGER, True), (Role.FRONT_DESK, True), (Role.HOUSEKEEPING, False),
])
def test_can_view_page_follows_view_allowed_roles(hotel_a, role, expected):
    membership = MembershipFactory(property=hotel_a, role=role)
    assert can_view_page(membership, 'tape_chart') is expected


@pytest.mark.parametrize('role,expected', [
    (Role.OWNER, True), (Role.MANAGER, True), (Role.FRONT_DESK, False), (Role.HOUSEKEEPING, False),
])
def test_dashboard_menu_follows_the_dashboard_view_roles(hotel_a, role, expected):
    membership = MembershipFactory(property=hotel_a, role=role)
    assert can_view_page(membership, 'dashboard') is expected


def test_can_view_page_fails_closed(hotel_a):
    membership = MembershipFactory(property=hotel_a, role=Role.OWNER)
    assert can_view_page(None, 'tape_chart') is False
    assert can_view_page(membership, 'unknown_page') is False


# ---------- booking_id on bars ----------

def test_multi_room_booking_bars_share_booking_pk(hotel_a, hotel_b):
    first = stay(hotel_a)
    BookingRoomFactory(booking=first.booking)
    foreign = stay(hotel_b)
    bars = [bar for row in build_tape_chart(hotel_a)['rows'] for bar in row['bars']]
    assert len(bars) == 2
    assert [bar['booking_id'] for bar in bars] == [first.booking.pk] * 2
    assert foreign.booking.pk not in {bar['booking_id'] for bar in bars}


@pytest.mark.parametrize('headers', [{}, {'HTTP_HX_REQUEST': 'true'}], ids=['full', 'htmx'])
def test_bars_render_booking_id_without_foreign_ids(client, hotel_a, hotel_b, login_member, headers):
    login_member(hotel_a)
    mine = stay(hotel_a)
    other = stay(hotel_b)
    content = client.get(tape_url(hotel_a), **headers).content.decode()
    assert 'data-booking-id="%s"' % mine.booking.pk in content
    assert str(other.booking.pk) not in content
