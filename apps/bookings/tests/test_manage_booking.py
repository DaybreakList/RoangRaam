"""
Guest "my booking" pages: look a booking up with its code and email, then cancel it.

Stripe is patched where payments.py imports it (the same place as test_cancellation.py),
so nothing here reaches Stripe.
"""

import html as html_lib
import itertools
import re
from datetime import timedelta
from decimal import Decimal
from unittest import mock

import pytest
import stripe
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.bookings.manage_views import (
    CANCELLED_MESSAGE,
    NOT_CANCELLABLE_MESSAGE,
    NOT_FOUND_MESSAGE,
    REFUNDED_MESSAGE,
)
from apps.bookings.models import Booking, BookingRoom, Payment
from apps.bookings.payments import CHECKOUT_DEADLINE, REFUND_FAILED_MESSAGE
from apps.bookings.services import get_available_room_types
from apps.rooms.models import DailyRate, RoomType
from apps.rooms.tests.factories import DailyRateFactory, RoomFactory, RoomTypeFactory

from .factories import BookingFactory, BookingNightRateFactory, BookingRoomFactory, GuestFactory

pytestmark = pytest.mark.django_db

CODE = 'ABCD2345'
EMAIL = 'somchai@example.com'
PHONE = '0899998888'
# Two different nightly prices, so a total can only come from the per-night rows
NIGHT_PRICES = (Decimal('1000.00'), Decimal('1500.00'))
ROOM_NUMBER_PREFIX = 'SECRETNO'
CANCEL_CONFIRMED_TEXT = 'ยืนยันการยกเลิก'
room_numbers = itertools.count(1)


def day(offset):
    return timezone.localdate() + timedelta(days=offset)


def url(name, prop):
    return reverse(f'bookings:{name}', kwargs={'property_slug': prop.slug})


def make_booking(
    prop, *, status=Booking.Status.CONFIRMED, payment_option=Booking.PaymentOption.FULL,
    code=CODE, email=EMAIL, rooms=2, type_name='Deluxe', room_type=None,
):
    """A booking of `prop` for days 10-12 holding `rooms` rooms of one type, priced per night."""
    check_in = day(10)
    check_out = check_in + timedelta(days=len(NIGHT_PRICES))
    guest = GuestFactory(
        property=prop, email=email, first_name='Somchai', last_name='Jaidee', phone=PHONE,
    )
    booking = BookingFactory(
        property=prop, guest=guest, code=code, status=status, payment_option=payment_option,
        check_in_date=check_in, check_out_date=check_out, adults=2, children=1,
        total_amount=sum(NIGHT_PRICES) * rooms,
    )
    room_type = (
        room_type
        or RoomType.objects.filter(property=prop, name=type_name).first()
        or RoomTypeFactory(property=prop, name=type_name)
    )
    for index, price in enumerate(NIGHT_PRICES):
        if not DailyRate.objects.filter(room_type=room_type, date=check_in + timedelta(days=index)).exists():
            DailyRateFactory(property=prop, room_type=room_type, date=check_in + timedelta(days=index), price=price)
    for _ in range(rooms):
        room = RoomFactory(
            property=prop, room_type=room_type, number=f'{ROOM_NUMBER_PREFIX}{next(room_numbers)}',
        )
        booking_room = BookingRoomFactory(booking=booking, room=room)
        for index, price in enumerate(NIGHT_PRICES):
            BookingNightRateFactory(
                booking_room=booking_room, night=check_in + timedelta(days=index), price=price,
            )
    return booking


def credentials(booking=None, **overrides):
    data = {'code': booking.code if booking else CODE, 'email': booking.guest.email if booking else EMAIL}
    data.update(overrides)
    return data


def add_charge(booking, ref='pi_1'):
    return Payment.scoped.for_property(booking.property).create(
        property=booking.property, booking=booking, kind=Payment.Kind.CHARGE,
        amount=booking.total_amount, provider_ref=ref, idempotency_key=f'evt-{ref}',
    )


def refresh(booking):
    booking.refresh_from_db()
    return booking


def rooms_active(booking):
    return list(BookingRoom.objects.filter(booking=booking).values_list('is_active', flat=True))


def text(response):
    """The page as the guest reads it: HTML entities (such as &quot;) turned back into characters."""
    return html_lib.unescape(response.content.decode())


def without_values(response):
    """The page with every input value and CSRF token (form field and HTMX header) blanked out."""
    page = re.sub(r'value="[^"]*"', 'value=""', response.content.decode())
    return re.sub(r'"X-CSRFToken": "[^"]*"', '"X-CSRFToken": ""', page)


@pytest.fixture
def stripe_refund():
    with mock.patch('apps.bookings.payments.stripe.Refund.create') as create:
        create.return_value = mock.Mock(id='re_test_1')
        yield create


@pytest.fixture
def lookup(client, hotel_a):
    return lambda **data: client.post(url('manage-booking', hotel_a), data)


@pytest.fixture
def cancel(client, hotel_a):
    return lambda **data: client.post(url('manage-booking-cancel', hotel_a), data)


class TestLookup:
    def test_code_and_email_match_ignoring_case_and_spaces(self, hotel_a, lookup):
        make_booking(hotel_a)

        response = lookup(code=f'  {CODE.lower()} ', email=f'  {EMAIL.upper()}  ')

        assert response.status_code == 200
        html = response.content.decode()
        assert CODE in html
        assert 'Deluxe × 2' in html
        # The price of each night, not one price times the nights
        assert '1,000.00' in html and '1,500.00' in html
        assert '5,000.00' in html
        assert 'Somchai Jaidee' in html and EMAIL in html
        assert 'ผู้ใหญ่ 2 คน · เด็ก 1 คน' in html
        assert 'ชำระเต็มจำนวน' in html

    def test_details_never_show_room_numbers_or_phone(self, hotel_a, lookup):
        make_booking(hotel_a)

        html = lookup(**credentials()).content.decode()

        assert ROOM_NUMBER_PREFIX not in html
        assert PHONE not in html

    def test_details_show_the_hotel_check_in_and_out_times(self, hotel_a, lookup):
        make_booking(hotel_a)

        html = lookup(**credentials()).content.decode()

        assert f'{hotel_a.check_in_time:%H:%M} น.' in html
        assert f'{hotel_a.check_out_time:%H:%M} น.' in html

    def test_wrong_code_wrong_email_and_other_hotel_look_the_same(self, hotel_a, hotel_b, lookup):
        make_booking(hotel_a)
        other = make_booking(hotel_b, code='HOTELB22', email='b-guest@example.com', type_name='Beta Suite')

        wrong_code = lookup(code='ZZZZ9999', email=EMAIL)
        wrong_email = lookup(code=CODE, email='nobody@example.com')
        other_hotel = lookup(**credentials(other))

        responses = [wrong_code, wrong_email, other_hotel]
        assert {response.status_code for response in responses} == {200}
        assert len({without_values(response) for response in responses}) == 1
        for response in responses:
            html = response.content.decode()
            assert NOT_FOUND_MESSAGE in html
            assert 'Beta Suite' not in html and 'Hotel B' not in html
            assert 'Deluxe' not in html and 'Jaidee' not in html

    def test_get_shows_an_empty_form(self, client, hotel_a):
        make_booking(hotel_a)

        response = client.get(url('manage-booking', hotel_a))

        assert response.status_code == 200
        assert not response.context['form'].is_bound
        html = response.content.decode()
        assert NOT_FOUND_MESSAGE not in html
        assert 'name="code"' in html and 'name="email"' in html

    def test_query_string_is_ignored(self, client, hotel_a):
        make_booking(hotel_a)

        response = client.get(url('manage-booking', hotel_a), {'code': CODE, 'email': EMAIL})

        assert response.status_code == 200
        html = response.content.decode()
        assert not response.context['form'].is_bound
        assert CODE not in html and EMAIL not in html and 'Jaidee' not in html

    def test_missing_fields_show_field_errors_not_a_booking(self, hotel_a, lookup):
        make_booking(hotel_a)

        response = lookup(code='', email='')

        assert response.status_code == 200
        assert response.context['form'].errors.keys() == {'code', 'email'}
        assert 'Jaidee' not in response.content.decode()

    def test_page_is_open_to_guests_and_takes_get_and_post_only(self, client, hotel_a):
        assert client.put(url('manage-booking', hotel_a)).status_code == 405
        assert client.delete(url('manage-booking', hotel_a)).status_code == 405

    def test_page_for_an_unknown_hotel_is_404(self, client):
        assert client.get('/p/no-such-hotel/my-booking/').status_code == 404

    def test_forms_on_the_page_do_not_put_code_or_email_in_a_url(self, hotel_a, lookup):
        make_booking(hotel_a)

        html = lookup(**credentials()).content.decode()

        targets = re.findall(r'(?:href|action)="([^"]*)"', html)
        assert targets
        assert not [target for target in targets if '?' in target or CODE in target or EMAIL in target]


class TestStatusSpecificDetails:
    def test_pending_full_booking_has_a_pay_button_to_the_pay_route(self, hotel_a, lookup):
        booking = make_booking(hotel_a, status=Booking.Status.PENDING_PAYMENT)

        html = lookup(**credentials()).content.decode()

        pay_url = reverse('bookings:pay', kwargs={'property_slug': hotel_a.slug, 'booking_id': booking.pk})
        assert f'action="{pay_url}"' in html
        assert 'ชำระเงิน</button>' in html
        assert 'csrfmiddlewaretoken' in html

    def test_pay_button_is_hidden_once_the_checkout_deadline_has_passed(self, hotel_a, lookup):
        booking = make_booking(hotel_a, status=Booking.Status.PENDING_PAYMENT)
        Booking.objects.filter(pk=booking.pk).update(
            created_at=timezone.now() - CHECKOUT_DEADLINE - timedelta(minutes=1),
        )

        html = lookup(**credentials()).content.decode()

        assert '/pay/' not in html
        assert 'หมดเวลาชำระเงิน' in html
        # The guest can still give the booking up
        assert url('manage-booking-cancel', hotel_a) in html

    def test_confirmed_and_pay_at_hotel_bookings_have_no_pay_button(self, hotel_a, lookup):
        make_booking(hotel_a)
        make_booking(
            hotel_a, code='HOTEL222', email='other@example.com',
            payment_option=Booking.PaymentOption.PAY_AT_HOTEL,
        )

        for data in (credentials(), credentials(code='HOTEL222', email='other@example.com')):
            html = lookup(**data).content.decode()
            assert '/pay/' not in html
            assert url('manage-booking-cancel', hotel_a) in html

    @pytest.mark.parametrize('status', [Booking.Status.CHECKED_IN, Booking.Status.CHECKED_OUT])
    def test_booking_past_confirmation_has_no_cancel_button(self, hotel_a, lookup, status):
        make_booking(hotel_a, status=status)

        html = lookup(**credentials()).content.decode()

        assert url('manage-booking-cancel', hotel_a) not in html
        assert '/pay/' not in html

    def test_cancelled_booking_shows_the_neutral_message_and_a_link_to_search(self, hotel_a, lookup):
        make_booking(hotel_a, status=Booking.Status.CANCELLED)

        html = lookup(**credentials()).content.decode()

        assert 'การจองนี้ถูกยกเลิกแล้ว' in html
        assert 'หากต้องการเข้าพัก กรุณาทำการจองใหม่' in html
        assert url('search', hotel_a) in html
        assert url('manage-booking-cancel', hotel_a) not in html


class TestCancelPayAtHotel:
    def test_step_one_asks_for_confirmation_and_changes_nothing(self, hotel_a, cancel, stripe_refund):
        booking = make_booking(hotel_a, payment_option=Booking.PaymentOption.PAY_AT_HOTEL)

        response = cancel(**credentials())

        assert response.status_code == 200
        assert 'bookings/manage_booking_cancel.html' in [t.name for t in response.templates]
        html = response.content.decode()
        assert 'ไม่มีค่าใช้จ่าย' in html
        assert CANCEL_CONFIRMED_TEXT in html
        assert 'Deluxe × 2' in html
        assert ROOM_NUMBER_PREFIX not in html and PHONE not in html
        assert refresh(booking).status == Booking.Status.CONFIRMED
        assert booking.cancelled_at is None
        assert rooms_active(booking) == [True, True]
        stripe_refund.assert_not_called()

    def test_back_button_posts_to_the_details_page_with_hidden_fields(self, hotel_a, cancel):
        make_booking(hotel_a)

        html = cancel(**credentials()).content.decode()

        back_form = re.search(
            rf'<form method="post" action="{url("manage-booking", hotel_a)}">(.*?)</form>', html, re.S,
        )
        assert back_form is not None
        assert f'name="code" value="{CODE}"' in back_form.group(1)
        assert f'name="email" value="{EMAIL}"' in back_form.group(1)
        assert 'name="confirm"' not in back_form.group(1)

    def test_step_two_cancels_and_releases_the_rooms(self, hotel_a, cancel):
        booking = make_booking(hotel_a, payment_option=Booking.PaymentOption.PAY_AT_HOTEL, rooms=1)
        room_type = booking.booking_rooms.get().room_type
        assert room_type not in get_available_room_types(hotel_a, booking.check_in_date, booking.check_out_date)

        response = cancel(**credentials(), confirm='1')

        assert response.status_code == 200
        html = response.content.decode()
        assert CANCELLED_MESSAGE in html
        assert REFUNDED_MESSAGE not in html
        assert 'การจองนี้ถูกยกเลิกแล้ว' in html
        refresh(booking)
        assert booking.status == Booking.Status.CANCELLED
        assert booking.cancelled_at is not None
        assert rooms_active(booking) == [False]
        assert room_type in get_available_room_types(hotel_a, booking.check_in_date, booking.check_out_date)

    def test_cancelling_all_rooms_of_a_multi_room_booking(self, hotel_a, cancel):
        booking = make_booking(hotel_a, payment_option=Booking.PaymentOption.PAY_AT_HOTEL, rooms=3)

        cancel(**credentials(), confirm='1')

        assert rooms_active(booking) == [False, False, False]


class TestCancelFull:
    def test_paid_booking_is_refunded_in_full(self, hotel_a, cancel, stripe_refund):
        booking = make_booking(hotel_a)
        add_charge(booking)

        step_one = cancel(**credentials()).content.decode()
        assert 'ระบบจะคืนเงินเต็มจำนวน 5,000.00 บาท ไปยังช่องทางที่ชำระ' in step_one
        stripe_refund.assert_not_called()
        assert refresh(booking).status == Booking.Status.CONFIRMED

        response = cancel(**credentials(), confirm='1')

        html = response.content.decode()
        assert CANCELLED_MESSAGE in html and REFUNDED_MESSAGE in html
        stripe_refund.assert_called_once_with(
            api_key=mock.ANY, payment_intent='pi_1', idempotency_key='refund-pi_1',
        )
        assert refresh(booking).status == Booking.Status.CANCELLED
        assert rooms_active(booking) == [False, False]
        kinds = list(Payment.scoped.for_property(hotel_a).filter(booking=booking).values_list('kind', flat=True))
        assert sorted(kinds) == [Payment.Kind.CHARGE, Payment.Kind.REFUND]

    def test_failed_refund_shows_the_message_and_changes_nothing(self, hotel_a, cancel, stripe_refund):
        booking = make_booking(hotel_a)
        add_charge(booking)
        stripe_refund.side_effect = stripe.APIConnectionError('down')

        response = cancel(**credentials(), confirm='1')

        assert response.status_code == 200
        html = response.content.decode()
        assert 'คืนเงินไม่สำเร็จ กรุณาลองใหม่อีกครั้ง' in html
        assert REFUND_FAILED_MESSAGE in html
        assert CANCELLED_MESSAGE not in html
        assert refresh(booking).status == Booking.Status.CONFIRMED
        assert booking.cancelled_at is None
        assert rooms_active(booking) == [True, True]
        # The guest can try again from the same page
        assert url('manage-booking-cancel', hotel_a) in html

        stripe_refund.side_effect = None
        cancel(**credentials(), confirm='1')
        assert refresh(booking).status == Booking.Status.CANCELLED

    def test_unpaid_booking_says_nothing_was_paid_and_calls_no_refund(self, hotel_a, cancel, stripe_refund):
        booking = make_booking(hotel_a, status=Booking.Status.PENDING_PAYMENT)

        step_one = cancel(**credentials()).content.decode()
        assert 'ยังไม่มีการชำระเงิน' in step_one
        assert refresh(booking).status == Booking.Status.PENDING_PAYMENT

        response = cancel(**credentials(), confirm='1')

        html = response.content.decode()
        assert CANCELLED_MESSAGE in html
        assert REFUNDED_MESSAGE not in html
        assert refresh(booking).status == Booking.Status.CANCELLED
        stripe_refund.assert_not_called()
        assert not Payment.scoped.for_property(hotel_a).filter(booking=booking).exists()


class TestCancelNotAllowed:
    @pytest.mark.parametrize('status', [Booking.Status.CHECKED_IN, Booking.Status.CHECKED_OUT])
    def test_forced_step_two_shows_a_thai_error_and_changes_nothing(
        self, hotel_a, cancel, stripe_refund, status,
    ):
        booking = make_booking(hotel_a, status=status)

        response = cancel(**credentials(), confirm='1')

        assert response.status_code == 200
        assert f'ไม่สามารถยกเลิกการจองที่มีสถานะ "{booking.get_status_display()}" ได้' in text(response)
        assert refresh(booking).status == status
        assert booking.cancelled_at is None
        assert rooms_active(booking) == [True, True]
        stripe_refund.assert_not_called()

    def test_forced_step_one_does_not_show_the_confirmation_page(self, hotel_a, cancel):
        booking = make_booking(hotel_a, status=Booking.Status.CHECKED_IN)

        response = cancel(**credentials())

        html = response.content.decode()
        assert NOT_CANCELLABLE_MESSAGE in html
        assert CANCEL_CONFIRMED_TEXT not in html
        assert refresh(booking).status == Booking.Status.CHECKED_IN

    def test_cancelling_twice_shows_an_error_not_a_500(self, hotel_a, cancel):
        booking = make_booking(hotel_a, payment_option=Booking.PaymentOption.PAY_AT_HOTEL)
        cancel(**credentials(), confirm='1')
        cancelled_at = refresh(booking).cancelled_at

        response = cancel(**credentials(), confirm='1')

        assert response.status_code == 200
        html = text(response)
        assert 'ไม่สามารถยกเลิกการจองที่มีสถานะ "ยกเลิกแล้ว" ได้' in html
        assert CANCELLED_MESSAGE not in html
        assert refresh(booking).cancelled_at == cancelled_at

    def test_step_one_on_a_cancelled_booking_shows_its_details(self, hotel_a, cancel):
        make_booking(hotel_a, status=Booking.Status.CANCELLED)

        response = cancel(**credentials())

        html = response.content.decode()
        assert 'การจองนี้ถูกยกเลิกแล้ว' in html
        assert CANCEL_CONFIRMED_TEXT not in html


class TestWrongCredentials:
    def test_step_two_with_wrong_email_finds_nothing_and_changes_nothing(self, hotel_a, cancel, stripe_refund):
        booking = make_booking(hotel_a)
        add_charge(booking)

        response = cancel(**credentials(email='nobody@example.com'), confirm='1')

        assert response.status_code == 200
        assert NOT_FOUND_MESSAGE in response.content.decode()
        assert refresh(booking).status == Booking.Status.CONFIRMED
        assert rooms_active(booking) == [True, True]
        stripe_refund.assert_not_called()

    def test_step_one_with_wrong_code_shows_the_same_answer_as_the_details_page(self, hotel_a, cancel, lookup):
        make_booking(hotel_a)

        from_cancel = cancel(**credentials(code='ZZZZ9999'))
        from_lookup = lookup(**credentials(code='ZZZZ9999'))

        assert from_cancel.status_code == from_lookup.status_code == 200
        assert without_values(from_cancel) == without_values(from_lookup)

    def test_nothing_is_cancelled_without_the_confirm_field(self, hotel_a, cancel):
        booking = make_booking(hotel_a)

        cancel(**credentials())
        cancel(**credentials(), confirmed='yes')

        assert refresh(booking).status == Booking.Status.CONFIRMED


class TestIsolation:
    def test_cancel_with_another_hotels_credentials_changes_nothing(
        self, hotel_a, hotel_b, cancel, stripe_refund,
    ):
        other = make_booking(hotel_b, code='HOTELB22', email='b-guest@example.com', type_name='Beta Suite')
        add_charge(other)

        step_one = cancel(**credentials(other))
        step_two = cancel(**credentials(other), confirm='1')

        for response in (step_one, step_two):
            assert response.status_code == 200
            html = response.content.decode()
            assert NOT_FOUND_MESSAGE in html
            assert 'Beta Suite' not in html and 'Hotel B' not in html
        assert refresh(other).status == Booking.Status.CONFIRMED
        assert other.cancelled_at is None
        assert rooms_active(other) == [True, True]
        stripe_refund.assert_not_called()
        assert Payment.scoped.for_property(hotel_b).filter(booking=other).count() == 1

    def test_hotels_can_share_a_booking_code(self, hotel_a, hotel_b, lookup):
        # The code is only unique inside one hotel, so the slug picks whose booking is meant
        make_booking(hotel_a, email='a-guest@example.com', type_name='Alpha Room')
        make_booking(hotel_b, email='b-guest@example.com', type_name='Beta Suite')

        html = lookup(code=CODE, email='a-guest@example.com').content.decode()
        assert 'Alpha Room' in html and 'Beta Suite' not in html

        mixed_up = lookup(code=CODE, email='b-guest@example.com').content.decode()
        assert NOT_FOUND_MESSAGE in mixed_up and 'Beta Suite' not in mixed_up


class TestMethodsAndCsrf:
    def test_get_on_the_cancel_route_is_405(self, client, hotel_a):
        make_booking(hotel_a)

        response = client.get(url('manage-booking-cancel', hotel_a), credentials())

        assert response.status_code == 405

    @pytest.mark.parametrize('method', ['put', 'patch', 'delete'])
    def test_other_methods_on_the_cancel_route_are_405(self, client, hotel_a, method):
        assert getattr(client, method)(url('manage-booking-cancel', hotel_a)).status_code == 405

    def test_post_without_a_csrf_token_is_403(self, hotel_a):
        booking = make_booking(hotel_a)
        strict = Client(enforce_csrf_checks=True)

        lookup_response = strict.post(url('manage-booking', hotel_a), credentials())
        cancel_response = strict.post(url('manage-booking-cancel', hotel_a), {**credentials(), 'confirm': '1'})

        assert lookup_response.status_code == 403
        assert cancel_response.status_code == 403
        assert refresh(booking).status == Booking.Status.CONFIRMED
        assert rooms_active(booking) == [True, True]

    def test_post_with_a_csrf_token_works(self, hotel_a):
        make_booking(hotel_a)
        strict = Client(enforce_csrf_checks=True)
        page = strict.get(url('manage-booking', hotel_a))
        token = page.cookies['csrftoken'].value

        response = strict.post(
            url('manage-booking', hotel_a), {**credentials(), 'csrfmiddlewaretoken': token},
        )

        assert response.status_code == 200
        assert 'Deluxe × 2' in response.content.decode()

    def test_nothing_is_kept_in_the_session(self, client, hotel_a):
        make_booking(hotel_a)

        client.post(url('manage-booking', hotel_a), credentials())

        assert not dict(client.session)


class TestQueries:
    def test_details_page_query_count_does_not_depend_on_the_number_of_rooms(self, client, hotel_a):
        one = make_booking(hotel_a, rooms=1, code='ONEROOM2', email='one@example.com')
        many = make_booking(hotel_a, rooms=5, code='FIVEROOM', email='five@example.com')
        many_types = make_booking(
            hotel_a, rooms=3, code='MANYTYPE', email='types@example.com', type_name='Suite',
        )
        extra = RoomTypeFactory(property=hotel_a, name='Twin')
        for index in range(2):
            booking_room = BookingRoomFactory(
                booking=many_types, room=RoomFactory(property=hotel_a, room_type=extra),
            )
            BookingNightRateFactory(booking_room=booking_room, night=many_types.check_in_date, price=Decimal('900.00'))

        def count_queries(booking):
            with CaptureQueriesContext(connection) as queries:
                response = client.post(url('manage-booking', hotel_a), credentials(booking))
                assert response.status_code == 200
                assert booking.code in response.content.decode()
            return len(queries)

        count_queries(one)  # warm-up
        assert count_queries(many) == count_queries(one)
        assert count_queries(many_types) == count_queries(one)


class TestLinks:
    def test_search_page_links_to_my_booking(self, client, hotel_a):
        response = client.get(url('search', hotel_a))

        html = response.content.decode()
        assert f'href="{url("manage-booking", hotel_a)}"' in html
        assert 'ดูการจองของฉัน' in html

    def test_search_link_is_outside_the_results_and_the_book_form(self, client, hotel_a):
        html = client.get(url('search', hotel_a)).content.decode()

        link = html.index(url('manage-booking', hotel_a))
        assert link < html.index('id="results"')
        assert link < html.index('id="book-form"')

    def test_confirmation_page_links_to_my_booking(self, client, hotel_a):
        booking = make_booking(hotel_a, payment_option=Booking.PaymentOption.PAY_AT_HOTEL)

        response = client.get(reverse(
            'bookings:booking-confirmed', kwargs={'property_slug': hotel_a.slug, 'booking_id': booking.pk},
        ))

        html = response.content.decode()
        assert response.status_code == 200
        assert f'href="{url("manage-booking", hotel_a)}"' in html
        assert 'ดูการจองของฉัน' in html
        # The link carries nothing about the booking
        assert booking.code not in url('manage-booking', hotel_a)
