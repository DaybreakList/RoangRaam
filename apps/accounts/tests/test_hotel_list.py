"""Public home page at /: a list of active hotels for guests."""
import pytest
from django.urls import reverse

from apps.accounts.services import public_hotels
from apps.properties.models import Membership
from apps.properties.tests.factories import MembershipFactory, PropertyFactory

pytestmark = pytest.mark.django_db


def test_root_is_the_hotel_list():
    assert reverse('hotel_list') == '/'


def test_guest_sees_active_hotels_with_their_search_links(client):
    river = PropertyFactory(name='Riverside', slug='riverside', address='ริมน้ำ')
    hill = PropertyFactory(name='Hilltop', slug='hilltop')
    response = client.get('/')
    assert response.status_code == 200
    body = response.content.decode()
    assert [hotel.name for hotel in response.context['hotels']] == ['Hilltop', 'Riverside']
    for hotel in (river, hill):
        assert f'href="{reverse("bookings:search", kwargs={"property_slug": hotel.slug})}"' in body
    assert 'ริมน้ำ' in body
    # Guests reach the staff login from the navbar button
    assert f'href="{reverse("accounts:login")}"' in body
    assert 'เข้าสู่ระบบพนักงาน' in body


def test_inactive_hotels_are_hidden():
    PropertyFactory(name='Open', slug='open')
    PropertyFactory(name='Closed', slug='closed', is_active=False)
    assert [hotel.slug for hotel in public_hotels()] == ['open']


def test_page_with_no_active_hotel_says_so(client):
    PropertyFactory(slug='closed', is_active=False)
    response = client.get('/')
    assert response.status_code == 200
    assert 'ยังไม่มีโรงแรมเปิดให้จอง' in response.content.decode()
    assert '/p/closed/' not in response.content.decode()


def test_hotel_name_is_escaped(client):
    PropertyFactory(name='<script>alert(1)</script>', slug='xss')
    body = client.get('/').content.decode()
    assert '<script>alert(1)</script>' not in body
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in body


def test_query_count_does_not_grow_with_hotels(client, django_assert_max_num_queries):
    PropertyFactory.create_batch(2)
    with django_assert_max_num_queries(1) as few:
        client.get('/')
    PropertyFactory.create_batch(5)
    with django_assert_max_num_queries(len(few.captured_queries)):
        client.get('/')


def test_signed_in_staff_can_open_it_too(client):
    member = MembershipFactory(role=Membership.Role.FRONT_DESK)
    client.force_login(member.user)
    response = client.get('/')
    assert response.status_code == 200
    body = response.content.decode()
    assert member.property.name in body
    # Signed-in staff get logout in the navbar instead of the login button
    assert 'เข้าสู่ระบบพนักงาน' not in body
    assert 'ออกจากระบบ' in body


def test_only_get_and_head_are_allowed(client):
    assert client.head('/').status_code == 200
    assert client.post('/').status_code == 405
