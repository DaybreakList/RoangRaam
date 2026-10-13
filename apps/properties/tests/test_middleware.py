import pytest

from apps.properties.models import Membership

from .factories import MembershipFactory, PropertyFactory, UserFactory
from .scoped_model import ScopedThing, scoped_thing_table  # noqa: F401

pytestmark = [pytest.mark.django_db, pytest.mark.urls('apps.properties.tests.urls')]

Role = Membership.Role


@pytest.fixture
def hotel_a():
    return PropertyFactory(slug='hotel-a')


@pytest.fixture
def hotel_b():
    return PropertyFactory(slug='hotel-b')


def login_as_member(client, prop, role=Role.FRONT_DESK):
    membership = MembershipFactory(property=prop, role=role)
    client.force_login(membership.user)
    return membership


class TestPropertyResolution:
    def test_member_gets_property_and_membership(self, client, hotel_a):
        login_as_member(client, hotel_a, Role.FRONT_DESK)
        response = client.get('/p/hotel-a/staff/')
        assert response.status_code == 200
        assert response.content == b'hotel-a:FRONT_DESK'

    def test_unknown_slug_is_404(self, client, hotel_a):
        login_as_member(client, hotel_a)
        assert client.get('/p/no-such-hotel/staff/').status_code == 404

    def test_inactive_property_is_404(self, client):
        prop = PropertyFactory(slug='closed', is_active=False)
        login_as_member(client, prop)
        assert client.get('/p/closed/staff/').status_code == 404

    def test_anonymous_redirected_to_login(self, client, hotel_a):
        response = client.get('/p/hotel-a/staff/')
        assert response.status_code == 302
        assert 'next=/p/hotel-a/staff/' in response.url

    def test_urls_outside_property_have_no_property(self, client):
        response = client.get('/outside/')
        assert response.content == b'None'


class TestIsolation:
    def test_member_of_other_property_gets_404(self, client, hotel_a, hotel_b):
        login_as_member(client, hotel_b, Role.OWNER)
        assert client.get('/p/hotel-a/staff/').status_code == 404

    def test_logged_in_user_without_membership_gets_404(self, client, hotel_a):
        client.force_login(UserFactory())
        assert client.get('/p/hotel-a/staff/').status_code == 404

    def test_other_property_object_is_404(self, client, hotel_a, hotel_b, scoped_thing_table):
        login_as_member(client, hotel_a)
        own = ScopedThing.objects.create(property=hotel_a, name='mine')
        other = ScopedThing.objects.create(property=hotel_b, name='theirs')

        assert client.get(f'/p/hotel-a/things/{own.pk}/').content == b'mine'
        assert client.get(f'/p/hotel-a/things/{other.pk}/').status_code == 404


class TestPublicViews:
    @pytest.mark.parametrize('url', ['/p/hotel-a/public/', '/p/hotel-a/public-cbv/'])
    def test_anonymous_can_open_public_view(self, client, hotel_a, url):
        response = client.get(url)
        assert response.status_code == 200
        assert response.content == b'hotel-a'

    def test_public_view_still_404_for_unknown_slug(self, client):
        assert client.get('/p/no-such-hotel/public/').status_code == 404


class TestRoles:
    @pytest.mark.parametrize('url', ['/p/hotel-a/manager/', '/p/hotel-a/manager-cbv/'])
    def test_allowed_role_passes(self, client, hotel_a, url):
        login_as_member(client, hotel_a, Role.MANAGER)
        assert client.get(url).status_code == 200

    @pytest.mark.parametrize('url', ['/p/hotel-a/manager/', '/p/hotel-a/manager-cbv/'])
    def test_insufficient_role_is_403(self, client, hotel_a, url):
        login_as_member(client, hotel_a, Role.HOUSEKEEPING)
        assert client.get(url).status_code == 403

    def test_mixin_without_allowed_roles_denies_everyone(self, client, hotel_a):
        login_as_member(client, hotel_a, Role.OWNER)
        assert client.get('/p/hotel-a/no-roles-cbv/').status_code == 403
