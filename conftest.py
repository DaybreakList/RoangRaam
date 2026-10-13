# Shared pytest fixtures for all apps go here.

import pytest

from apps.properties.models import Membership
from apps.properties.tests.factories import MembershipFactory, PropertyFactory


@pytest.fixture
def hotel_a(db):
    return PropertyFactory(name='Hotel A', slug='hotel-a')


@pytest.fixture
def hotel_b(db):
    return PropertyFactory(name='Hotel B', slug='hotel-b')


@pytest.fixture
def login_member(client):
    """Create a membership in `prop` and log `client` in as that user."""

    def login(prop, role=Membership.Role.FRONT_DESK):
        membership = MembershipFactory(property=prop, role=role)
        client.force_login(membership.user)
        return membership

    return login


@pytest.fixture
def assert_404_all_methods(client):
    """Isolation check: every HTTP method on `url` must return 404, never 403 or 200."""

    def check(url):
        for method in ('get', 'post', 'patch', 'delete'):
            response = getattr(client, method)(url)
            assert response.status_code == 404, (
                f'{method.upper()} {url} returned {response.status_code}, expected 404'
            )

    return check
