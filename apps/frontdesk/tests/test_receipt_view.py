import uuid

import pytest
from django.urls import reverse

from apps.bookings.folio import close_bill, record_desk_payment
from apps.frontdesk.tests.test_folio_views import in_house, url
from apps.properties.models import Membership

pytestmark = pytest.mark.django_db

Role = Membership.Role


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / 'media'
    return settings.MEDIA_ROOT


def closed(hotel):
    booking = in_house(hotel)
    record_desk_payment(hotel, booking.pk, user=None, idempotency_key=uuid.uuid4())
    close_bill(hotel, booking.pk, user=None)
    return booking


def test_download_the_receipt(client, hotel_a, login_member):
    booking = closed(hotel_a)
    login_member(hotel_a)

    response = client.get(url('receipt', booking))

    assert response.status_code == 200
    assert response['Content-Type'] == 'application/pdf'
    assert response['Content-Disposition'] == f'inline; filename="receipt-1-{booking.code}.pdf"'
    assert response['Cache-Control'] == 'private, no-store'
    assert response.content.startswith(b'%PDF-')


def test_closed_folio_links_to_the_receipt(client, hotel_a, login_member):
    booking = closed(hotel_a)
    login_member(hotel_a)

    html = client.get(url('detail', booking)).content.decode()

    assert url('receipt', booking) in html
    assert 'ดาวน์โหลดใบเสร็จ (PDF)' in html


def test_open_folio_has_no_receipt_link_and_the_pdf_is_404(client, hotel_a, login_member):
    booking = in_house(hotel_a)
    login_member(hotel_a)

    assert url('receipt', booking) not in client.get(url('detail', booking)).content.decode()
    assert client.get(url('receipt', booking)).status_code == 404


def test_receipt_runs_a_fixed_number_of_queries(client, hotel_a, login_member, django_assert_max_num_queries):
    booking = closed(hotel_a)
    login_member(hotel_a)

    with django_assert_max_num_queries(10):
        client.get(url('receipt', booking))


def test_the_pdf_is_not_saved_anywhere(client, hotel_a, login_member, media_root):
    booking = closed(hotel_a)
    login_member(hotel_a)

    client.get(url('receipt', booking))

    assert not media_root.exists() or not any(media_root.rglob('*'))


def test_only_get(client, hotel_a, login_member):
    booking = closed(hotel_a)
    login_member(hotel_a)

    assert client.post(url('receipt', booking)).status_code == 405


def test_housekeeping_gets_403(client, hotel_a, login_member):
    booking = closed(hotel_a)
    login_member(hotel_a, role=Role.HOUSEKEEPING)

    assert client.get(url('receipt', booking)).status_code == 403


def test_receipt_of_another_hotel_is_404(client, hotel_a, hotel_b, login_member):
    # Owner of hotel A, own hotel in the URL, booking id of hotel B
    other = closed(hotel_b)
    login_member(hotel_a, role=Role.OWNER)

    target = reverse('folio:receipt', kwargs={'property_slug': hotel_a.slug, 'booking_id': other.pk})

    assert client.get(target).status_code == 404


def test_receipt_under_another_hotels_slug_is_404(client, hotel_a, hotel_b, login_member):
    booking = closed(hotel_a)
    login_member(hotel_a, role=Role.OWNER)

    target = reverse('folio:receipt', kwargs={'property_slug': hotel_b.slug, 'booking_id': booking.pk})

    assert client.get(target).status_code == 404
