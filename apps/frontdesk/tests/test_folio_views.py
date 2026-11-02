import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from apps.bookings.folio import close_bill, record_desk_payment
from apps.bookings.models import Booking, Payment, Receipt
from apps.bookings.tests.factories import BookingFactory, BookingNightRateFactory, BookingRoomFactory
from apps.properties.models import Membership
from apps.rooms.tests.factories import RoomFactory

pytestmark = pytest.mark.django_db

Role = Membership.Role
Status = Booking.Status


def in_house(hotel, status=Status.CHECKED_IN, price='1070.00', nights=2):
    booking = BookingFactory(property=hotel, status=status, payment_option=Booking.PaymentOption.PAY_AT_HOTEL)
    booking.check_out_date = booking.check_in_date + timedelta(days=nights)
    booking.total_amount = Decimal(price) * nights
    booking.save()
    booking_room = BookingRoomFactory(booking=booking, room=RoomFactory(property=hotel, number='501'))
    for offset in range(nights):
        BookingNightRateFactory(
            booking_room=booking_room, night=booking.check_in_date + timedelta(days=offset), price=Decimal(price),
        )
    return booking


def url(name, booking):
    return reverse(f'folio:{name}', kwargs={'property_slug': booking.property.slug, 'booking_id': booking.pk})


def texts(response):
    return [str(message) for message in get_messages(response.wsgi_request)]


# --- page -------------------------------------------------------------------

def test_page_shows_nights_vat_and_balance(client, hotel_a, login_member):
    booking = in_house(hotel_a)
    login_member(hotel_a)

    response = client.get(url('detail', booking))

    assert response.status_code == 200
    html = response.content.decode()
    assert booking.code in html
    assert '501' in html
    assert '2,140.00' in html  # total and balance
    assert '140.00' in html  # VAT 7% inside the price
    assert response.context['can_pay'] is True
    assert response.context['can_close'] is False
    assert 'รับชำระ 2,140.00 บาท' in html


def test_page_runs_a_fixed_number_of_queries(client, hotel_a, login_member, django_assert_max_num_queries):
    booking = in_house(hotel_a, nights=5)
    login_member(hotel_a)

    with django_assert_max_num_queries(12):
        client.get(url('detail', booking))


def test_page_before_check_in_has_no_buttons(client, hotel_a, login_member):
    booking = in_house(hotel_a, status=Status.CONFIRMED)
    login_member(hotel_a)

    response = client.get(url('detail', booking))

    html = response.content.decode()
    assert 'รับชำระและปิดบิลได้หลังเช็คอิน' in html
    assert url('pay', booking) not in html
    assert url('close', booking) not in html


def test_page_of_a_closed_bill_shows_the_receipt_number(client, hotel_a, login_member):
    booking = in_house(hotel_a)
    record_desk_payment(hotel_a, booking.pk, user=None, idempotency_key=uuid.uuid4())
    close_bill(hotel_a, booking.pk, user=None)
    login_member(hotel_a)

    response = client.get(url('detail', booking))

    html = response.content.decode()
    assert 'ใบเสร็จเลขที่ <strong>1</strong>' in html
    assert url('pay', booking) not in html


def test_each_render_gets_a_new_payment_key(client, hotel_a, login_member):
    booking = in_house(hotel_a)
    login_member(hotel_a)

    first = client.get(url('detail', booking)).context['payment_key']
    second = client.get(url('detail', booking)).context['payment_key']

    assert first != second


def test_daily_list_links_to_the_folio(client, hotel_a, login_member):
    booking = in_house(hotel_a)
    login_member(hotel_a)

    response = client.get(reverse('daily_ops:index', kwargs={'property_slug': hotel_a.slug}))

    assert url('detail', booking) in response.content.decode()


# --- pay and close ----------------------------------------------------------

def test_pay_then_close(client, hotel_a, login_member):
    booking = in_house(hotel_a)
    member = login_member(hotel_a)

    response = client.post(url('pay', booking), {'idempotency_key': str(uuid.uuid4())})
    assert response.status_code == 302
    assert response.url == url('detail', booking)
    assert 'รับชำระ 2,140.00 บาท เรียบร้อยแล้ว' in texts(response)
    payment = Payment.objects.get(booking=booking)
    assert payment.source == Payment.Source.FRONT_DESK
    assert payment.recorded_by == member.user

    response = client.post(url('close', booking))
    assert 'ปิดบิลแล้ว ใบเสร็จเลขที่ 1' in texts(response)
    assert Receipt.objects.get(booking=booking).issued_by == member.user


def test_amount_sent_by_the_browser_is_ignored(client, hotel_a, login_member):
    booking = in_house(hotel_a)
    login_member(hotel_a)

    client.post(url('pay', booking), {'idempotency_key': str(uuid.uuid4()), 'amount': '1.00'})

    assert Payment.objects.get(booking=booking).amount == Decimal('2140.00')


def test_double_submit_is_recorded_once(client, hotel_a, login_member):
    booking = in_house(hotel_a)
    login_member(hotel_a)
    data = {'idempotency_key': str(uuid.uuid4())}

    client.post(url('pay', booking), data)
    client.post(url('pay', booking), data)

    assert Payment.objects.filter(booking=booking).count() == 1


def test_pay_without_a_key_shows_an_error(client, hotel_a, login_member):
    booking = in_house(hotel_a)
    login_member(hotel_a)

    response = client.post(url('pay', booking))

    assert response.status_code == 302
    assert texts(response) == ['คำขอไม่ถูกต้อง กรุณาโหลดหน้าใหม่แล้วลองอีกครั้ง']
    assert not Payment.objects.exists()


def test_close_with_money_due_shows_an_error(client, hotel_a, login_member):
    booking = in_house(hotel_a)
    login_member(hotel_a)

    response = client.post(url('close', booking))

    assert texts(response) == ['ยังมียอดค้างชำระ กรุณารับชำระให้ครบก่อนปิดบิล']
    assert not Receipt.objects.exists()


def test_page_only_answers_get(client, hotel_a, login_member):
    booking = in_house(hotel_a)
    login_member(hotel_a)

    assert client.post(url('detail', booking)).status_code == 405
    assert client.get(url('pay', booking)).status_code == 405
    assert client.get(url('close', booking)).status_code == 405


# --- roles and isolation ----------------------------------------------------

@pytest.mark.parametrize('name', ['detail', 'pay', 'close'])
def test_housekeeping_gets_403(client, hotel_a, login_member, name):
    booking = in_house(hotel_a)
    login_member(hotel_a, role=Role.HOUSEKEEPING)

    method = client.get if name == 'detail' else client.post
    assert method(url(name, booking)).status_code == 403


@pytest.mark.parametrize('name', ['detail', 'pay', 'close'])
def test_booking_of_another_hotel_is_404_and_nothing_changes(client, hotel_a, hotel_b, login_member, name):
    # Staff of hotel A, own hotel in the URL, but the booking id belongs to hotel B
    other = in_house(hotel_b)
    record_desk_payment(hotel_b, other.pk, user=None, idempotency_key=uuid.uuid4())
    login_member(hotel_a, role=Role.OWNER)
    target = reverse(f'folio:{name}', kwargs={'property_slug': hotel_a.slug, 'booking_id': other.pk})

    method = client.get if name == 'detail' else client.post
    response = method(target, {'idempotency_key': str(uuid.uuid4())} if name == 'pay' else {})

    assert response.status_code == 404
    assert Payment.objects.filter(booking=other).count() == 1
    assert not Receipt.objects.exists()
