from unittest.mock import patch

import pytest
from django.template.loader import render_to_string
from django.test import Client
from django.urls import reverse

from apps.properties.models import Membership
from apps.properties.tests.factories import MembershipFactory
from apps.rooms.tests.factories import RoomFactory

pytestmark = pytest.mark.django_db

Role = Membership.Role
HOME_LINK = 'กลับไปหน้าเลือกโรงแรม'


def home_url(hotel):
    return reverse('property_home', kwargs={'property_slug': hotel.slug})


# ---------- 404 ----------

def test_an_unknown_address_gets_the_thai_404(client):
    response = client.get('/no-such-page/')

    assert response.status_code == 404
    content = response.content.decode()
    assert 'ไม่พบหน้าที่คุณต้องการ' in content
    assert 'Not Found' not in content


def test_a_guest_gets_no_link_to_the_staff_pages(client):
    content = client.get('/no-such-page/').content.decode()

    assert HOME_LINK not in content


def test_a_signed_in_user_gets_a_way_back(client, hotel_a, login_member):
    login_member(hotel_a)

    content = client.get('/no-such-page/').content.decode()

    assert HOME_LINK in content
    assert f'href="{reverse("accounts:home")}"' in content


def test_the_404_of_another_hotel_never_shows_that_hotel(client, hotel_a, hotel_b, login_member):
    login_member(hotel_b, role=Role.OWNER)

    response = client.get(home_url(hotel_a))

    assert response.status_code == 404
    content = response.content.decode()
    assert hotel_a.name not in content
    assert hotel_a.slug not in content
    assert 'rr-navbar' not in content


def test_another_hotel_and_a_hotel_that_does_not_exist_look_the_same(client, hotel_a, hotel_b, login_member):
    login_member(hotel_b, role=Role.OWNER)

    existing = client.get(home_url(hotel_a))
    missing = client.get('/p/no-such-hotel/')

    assert existing.status_code == missing.status_code == 404
    assert existing.content.decode().split('<main', 1)[1] == missing.content.decode().split('<main', 1)[1]


# ---------- 403 ----------

def test_a_role_without_access_gets_the_thai_403(client, hotel_a, login_member):
    login_member(hotel_a, role=Role.HOUSEKEEPING)

    response = client.get(reverse('dashboard:hotel', kwargs={'property_slug': hotel_a.slug}))

    assert response.status_code == 403
    content = response.content.decode()
    assert 'คุณไม่มีสิทธิ์เข้าถึงหน้านี้' in content
    assert '403 Forbidden' not in content
    assert HOME_LINK in content


def test_an_expired_form_gets_the_thai_page_and_changes_nothing(hotel_a):
    room = RoomFactory(property=hotel_a)
    status = room.hk_status
    membership = MembershipFactory(property=hotel_a, role=Role.OWNER)
    client = Client(enforce_csrf_checks=True)
    client.force_login(membership.user)
    url = reverse('room_status:mark_clean', kwargs={'property_slug': hotel_a.slug, 'room_id': room.pk})

    response = client.post(url)

    assert response.status_code == 403
    content = response.content.decode()
    assert 'หน้านี้หมดอายุแล้ว' in content
    assert 'CSRF verification failed' not in content
    room.refresh_from_db()
    assert room.hk_status == status


# ---------- 500 ----------

def test_a_crash_gets_the_thai_500(hotel_a):
    membership = MembershipFactory(property=hotel_a)
    client = Client(raise_request_exception=False)
    client.force_login(membership.user)

    with patch('apps.accounts.views.render', side_effect=RuntimeError('boom')):
        response = client.get(home_url(hotel_a))

    assert response.status_code == 500
    content = response.content.decode()
    assert 'ระบบขัดข้องชั่วคราว' in content
    assert 'boom' not in content


def test_the_500_page_needs_no_request_and_no_database(django_assert_num_queries):
    with django_assert_num_queries(0):
        content = render_to_string('500.html')

    assert 'ระบบขัดข้องชั่วคราว' in content
    # Works when the static files are the problem too
    assert '<link' not in content and '<script' not in content
