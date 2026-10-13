import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.properties.models import Membership
from apps.properties.tests.factories import MembershipFactory, PropertyFactory, UserFactory

pytestmark = pytest.mark.django_db

Role = Membership.Role


def property_url(prop):
    return reverse('property_home', kwargs={'property_slug': prop.slug})


class TestPropertyList:
    def test_home_lists_only_own_properties(self, client, hotel_a, hotel_b, login_member):
        membership = login_member(hotel_a, Role.MANAGER)
        hotel_c = PropertyFactory(name='Hotel C', slug='hotel-c')
        MembershipFactory(user=membership.user, property=hotel_c, role=Role.OWNER)

        content = client.get(reverse('accounts:home')).content.decode()

        assert 'Hotel A' in content
        assert 'Hotel C' in content
        assert 'Hotel B' not in content
        assert property_url(hotel_a) in content
        assert property_url(hotel_b) not in content

    def test_user_without_membership_sees_empty_message(self, client, hotel_a):
        client.force_login(UserFactory())

        content = client.get(reverse('accounts:home')).content.decode()

        assert 'ยังไม่ได้เป็นสมาชิกของโรงแรมใด' in content
        assert 'Hotel A' not in content

    def test_inactive_property_is_hidden(self, client, hotel_a, login_member):
        membership = login_member(hotel_a)
        closed = PropertyFactory(name='Closed Hotel', slug='closed', is_active=False)
        MembershipFactory(user=membership.user, property=closed)

        content = client.get(reverse('accounts:home')).content.decode()

        assert 'Hotel A' in content
        assert 'Closed Hotel' not in content

    def test_query_count_does_not_grow_with_memberships(self, client, hotel_a, login_member):
        membership = login_member(hotel_a)
        url = reverse('accounts:home')

        with CaptureQueriesContext(connection) as one:
            client.get(url)
        for i in range(3):
            MembershipFactory(user=membership.user, property=PropertyFactory(slug=f'extra-{i}'))
        with CaptureQueriesContext(connection) as four:
            client.get(url)

        assert len(four) == len(one)


class TestSwitching:
    def test_property_home_shows_property_and_role(self, client, hotel_a, login_member):
        login_member(hotel_a, Role.HOUSEKEEPING)

        response = client.get(property_url(hotel_a))

        assert response.status_code == 200
        content = response.content.decode()
        assert 'Hotel A' in content
        assert 'แม่บ้าน' in content

    def test_switch_to_another_own_property(self, client, hotel_a, login_member):
        membership = login_member(hotel_a, Role.FRONT_DESK)
        hotel_c = PropertyFactory(name='Hotel C', slug='hotel-c')
        MembershipFactory(user=membership.user, property=hotel_c, role=Role.OWNER)

        # The switcher on hotel A's page links to hotel C
        assert property_url(hotel_c) in client.get(property_url(hotel_a)).content.decode()

        response = client.get(property_url(hotel_c))
        assert response.status_code == 200
        assert response.context['request'].property == hotel_c
        assert response.context['request'].membership.role == Role.OWNER

    def test_anonymous_redirected_to_login(self, client, hotel_a):
        response = client.get(property_url(hotel_a))

        assert response.status_code == 302
        assert response.url.startswith(reverse('accounts:login'))


class TestIsolation:
    def test_switcher_never_shows_other_property(self, client, hotel_a, hotel_b, login_member):
        login_member(hotel_a)

        content = client.get(property_url(hotel_a)).content.decode()

        assert 'Hotel B' not in content
        assert property_url(hotel_b) not in content

    def test_other_property_is_404_for_every_method(
        self, client, hotel_a, hotel_b, login_member, assert_404_all_methods
    ):
        login_member(hotel_a, Role.OWNER)

        assert_404_all_methods(property_url(hotel_b))

    def test_user_without_membership_gets_404(self, client, hotel_a, assert_404_all_methods):
        client.force_login(UserFactory())

        assert_404_all_methods(property_url(hotel_a))
