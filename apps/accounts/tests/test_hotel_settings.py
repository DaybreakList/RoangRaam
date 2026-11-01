import io
from datetime import time
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.properties.models import Membership, Property

pytestmark = pytest.mark.django_db

Role = Membership.Role

VALID = {
    'check_in_time': '15:00',
    'check_out_time': '11:00',
    'tax_rate': '10.00',
    'policy_text': 'งดสูบบุหรี่ในห้องพัก',
}


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def png(name='logo.png', size=(8, 8)):
    buffer = io.BytesIO()
    Image.new('RGB', size, 'teal').save(buffer, format='PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


def url(hotel):
    return reverse('hotel_settings:edit', kwargs={'property_slug': hotel.slug})


def refreshed(hotel):
    return Property.objects.get(pk=hotel.pk)


# --- page -------------------------------------------------------------------

def test_page_shows_the_saved_settings(client, hotel_a, login_member):
    hotel_a.policy_text = 'เช็คอินหลังบ่ายสองโมง'
    hotel_a.save()
    login_member(hotel_a, role=Role.MANAGER)

    response = client.get(url(hotel_a))

    assert response.status_code == 200
    html = response.content.decode()
    assert 'value="14:00"' in html
    assert 'value="12:00"' in html
    assert 'เช็คอินหลังบ่ายสองโมง' in html
    assert 'enctype="multipart/form-data"' in html


@pytest.mark.parametrize('role, shown', [(Role.OWNER, True), (Role.MANAGER, True), (Role.FRONT_DESK, False)])
def test_navbar_links_to_settings_for_owner_and_manager_only(client, hotel_a, login_member, role, shown):
    login_member(hotel_a, role=role)

    html = client.get(reverse('property_home', kwargs={'property_slug': hotel_a.slug})).content.decode()

    assert (url(hotel_a) in html) is shown


# --- save -------------------------------------------------------------------

@pytest.mark.parametrize('role', [Role.OWNER, Role.MANAGER])
def test_owner_and_manager_save_the_settings(client, hotel_a, login_member, role):
    login_member(hotel_a, role=role)

    response = client.post(url(hotel_a), VALID)

    assert response.status_code == 302
    assert response.url == url(hotel_a)
    hotel = refreshed(hotel_a)
    assert hotel.check_in_time == time(15, 0)
    assert hotel.check_out_time == time(11, 0)
    assert hotel.tax_rate == Decimal('10.00')
    assert hotel.policy_text == 'งดสูบบุหรี่ในห้องพัก'
    assert 'บันทึกการตั้งค่าโรงแรมแล้ว' in client.get(response.url).content.decode()


def test_logo_is_stored_under_the_property_folder(client, hotel_a, login_member, media_root):
    login_member(hotel_a, role=Role.OWNER)

    client.post(url(hotel_a), {**VALID, 'logo': png()})

    logo = refreshed(hotel_a).logo
    assert logo.name.startswith(f'properties/{hotel_a.pk}/')
    assert (media_root / logo.name).exists()


def test_logo_can_be_removed(client, hotel_a, login_member):
    login_member(hotel_a, role=Role.OWNER)
    client.post(url(hotel_a), {**VALID, 'logo': png()})

    client.post(url(hotel_a), {**VALID, 'logo-clear': 'on'})

    assert not refreshed(hotel_a).logo


def test_saving_without_a_new_file_keeps_the_logo(client, hotel_a, login_member):
    login_member(hotel_a, role=Role.OWNER)
    client.post(url(hotel_a), {**VALID, 'logo': png()})
    name = refreshed(hotel_a).logo.name

    client.post(url(hotel_a), {**VALID, 'policy_text': 'ใหม่'})

    assert refreshed(hotel_a).logo.name == name


def test_name_slug_and_limits_cannot_be_changed_here(client, hotel_a, login_member):
    login_member(hotel_a, role=Role.OWNER)

    client.post(url(hotel_a), {**VALID, 'name': 'Hacked', 'slug': 'hacked', 'max_rooms': '999', 'is_active': ''})

    hotel = refreshed(hotel_a)
    assert (hotel.name, hotel.slug, hotel.max_rooms, hotel.is_active) == ('Hotel A', 'hotel-a', None, True)


def test_save_does_not_overwrite_a_name_changed_meanwhile(client, hotel_a, login_member):
    # Only the form's fields are written, so a stale copy cannot undo another edit
    login_member(hotel_a, role=Role.OWNER)
    Property.objects.filter(pk=hotel_a.pk).update(name='Renamed by admin')

    client.post(url(hotel_a), VALID)

    assert refreshed(hotel_a).name == 'Renamed by admin'


# --- invalid input ----------------------------------------------------------

@pytest.mark.parametrize('field, value', [
    ('tax_rate', '-1'),
    ('tax_rate', '100.01'),
    ('tax_rate', 'abc'),
    ('check_in_time', '25:00'),
    ('check_out_time', ''),
])
def test_invalid_values_are_refused_and_nothing_changes(client, hotel_a, login_member, field, value):
    login_member(hotel_a, role=Role.OWNER)

    response = client.post(url(hotel_a), {**VALID, field: value})

    assert response.status_code == 400
    assert response.context['form'].errors[field]
    hotel = refreshed(hotel_a)
    assert (hotel.tax_rate, hotel.check_in_time, hotel.policy_text) == (Decimal('7.00'), time(14, 0), '')


def test_a_file_that_is_not_an_image_is_refused(client, hotel_a, login_member):
    login_member(hotel_a, role=Role.OWNER)
    fake = SimpleUploadedFile('logo.png', b'not really a png', content_type='image/png')

    response = client.post(url(hotel_a), {**VALID, 'logo': fake})

    assert response.status_code == 400
    assert 'logo' in response.context['form'].errors
    assert not refreshed(hotel_a).logo


def test_other_file_types_are_refused(client, hotel_a, login_member):
    login_member(hotel_a, role=Role.OWNER)
    buffer = io.BytesIO()
    Image.new('RGB', (8, 8)).save(buffer, format='GIF')

    response = client.post(url(hotel_a), {**VALID, 'logo': SimpleUploadedFile('logo.gif', buffer.getvalue())})

    assert response.status_code == 400
    assert not refreshed(hotel_a).logo


def test_a_logo_over_2_mb_is_refused(client, hotel_a, login_member, monkeypatch):
    monkeypatch.setattr('apps.accounts.forms.LOGO_MAX_BYTES', 50)
    login_member(hotel_a, role=Role.OWNER)

    response = client.post(url(hotel_a), {**VALID, 'logo': png()})

    assert response.status_code == 400
    assert response.context['form'].errors['logo'] == ['ไฟล์โลโก้ต้องไม่เกิน 2 MB']
    assert not refreshed(hotel_a).logo


def test_an_invalid_upload_does_not_reach_the_navbar(client, hotel_a, login_member):
    # The form fills its instance even when invalid; the page must keep the saved hotel
    login_member(hotel_a, role=Role.OWNER)

    response = client.post(url(hotel_a), {**VALID, 'tax_rate': '-1', 'logo': png()})

    assert response.status_code == 400
    assert not response.wsgi_request.property.logo
    assert 'class="rr-navbar__logo"' not in response.content.decode()


# --- roles and isolation ----------------------------------------------------

@pytest.mark.parametrize('role', [Role.FRONT_DESK, Role.HOUSEKEEPING])
def test_other_roles_get_403_and_nothing_changes(client, hotel_a, login_member, role):
    login_member(hotel_a, role=role)

    assert client.get(url(hotel_a)).status_code == 403
    assert client.post(url(hotel_a), VALID).status_code == 403
    assert refreshed(hotel_a).tax_rate == Decimal('7.00')


def test_owner_of_another_hotel_gets_404_and_nothing_changes(client, hotel_a, hotel_b, login_member):
    login_member(hotel_a, role=Role.OWNER)

    assert client.get(url(hotel_b)).status_code == 404
    assert client.post(url(hotel_b), {**VALID, 'logo': png()}).status_code == 404
    hotel = refreshed(hotel_b)
    assert hotel.tax_rate == Decimal('7.00')
    assert not hotel.logo


def test_saving_one_hotel_leaves_the_other_alone(client, hotel_a, hotel_b, login_member):
    login_member(hotel_a, role=Role.OWNER)

    client.post(url(hotel_a), VALID)

    assert refreshed(hotel_b).tax_rate == Decimal('7.00')
