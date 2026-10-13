import pytest
from django.urls import reverse

from apps.properties.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_login_with_correct_password_redirects_to_home(client):
    user = UserFactory(email='staff@example.com')

    response = client.post(
        reverse('accounts:login'),
        {'username': 'staff@example.com', 'password': 'password123'},
    )

    assert response.status_code == 302
    assert response.url == reverse('accounts:home')
    assert client.session['_auth_user_id'] == str(user.pk)


def test_login_with_wrong_password_shows_error(client):
    UserFactory(email='staff@example.com')

    response = client.post(
        reverse('accounts:login'),
        {'username': 'staff@example.com', 'password': 'wrong-password'},
    )

    assert response.status_code == 200
    assert response.context['form'].errors
    assert '_auth_user_id' not in client.session


def test_home_redirects_anonymous_user_to_login(client):
    response = client.get(reverse('accounts:home'))

    assert response.status_code == 302
    assert response.url.startswith(reverse('accounts:login'))


def test_logout_ends_session(client):
    client.force_login(UserFactory())

    response = client.post(reverse('accounts:logout'))

    assert response.url == reverse('accounts:login')
    assert '_auth_user_id' not in client.session