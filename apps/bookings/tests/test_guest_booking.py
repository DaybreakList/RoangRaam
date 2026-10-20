"""
Guest booking flow after the search page: choose rooms -> details -> confirm,
then Stripe (FULL) or the confirmation page (PAY_AT_HOTEL).

Stripe and the confirmation email are patched where booking_views imports them,
so nothing here reaches Stripe.
"""

import re
import uuid
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
import stripe
from django.core import mail
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.bookings.booking_views import (
    EMAIL_FAILED_MESSAGE,
    EMAIL_SENT_MESSAGE,
    INVALID_ROOMS_MESSAGE,
    NO_ROOMS_MESSAGE,
    ROOM_UNAVAILABLE_MESSAGE,
)
from apps.bookings.forms import STALE_FORM_MESSAGE, read_room_quantities
from apps.bookings.models import Booking, BookingRoom, Guest
from apps.bookings.payments import PAYMENT_WINDOW
from apps.bookings.services import MAX_ROOMS_PER_BOOKING, TOO_MANY_ROOMS_MESSAGE
from apps.properties.tests.factories import PropertyFactory
from apps.rooms.tests.factories import DailyRateFactory, RoomFactory, RoomTypeFactory

from .factories import BookingFactory, BookingRoomFactory, GuestFactory

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}
CHECKOUT_URL = 'https://checkout.stripe.test/session/cs_test_123'
EMAIL_LINE = 'ส่งอีเมลยืนยันไปที่'
GUEST = {'first_name': 'Somchai', 'last_name': 'Jaidee', 'email': 'somchai@example.com', 'phone': '0812345678'}


def day(offset):
    return timezone.localdate() + timedelta(days=offset)


def url(name, prop, **kwargs):
    return reverse(f'bookings:{name}', kwargs={'property_slug': prop.slug, **kwargs})


def make_room_type(prop, name='Deluxe', rooms=1, prices=(Decimal('1000.00'),) * 3, max_occupancy=2):
    """A room type with `rooms` rooms and one rate per night starting on day 10."""
    room_type = RoomTypeFactory(property=prop, name=name, max_occupancy=max_occupancy)
    for index in range(rooms):
        RoomFactory(property=prop, room_type=room_type, number=f'{name[:5]}-ROOMNO{index + 1}')
    for offset, price in enumerate(prices):
        DailyRateFactory(property=prop, room_type=room_type, date=day(10 + offset), price=price)
    return room_type


def stay(**overrides):
    query = {'check_in': day(10).isoformat(), 'check_out': day(13).isoformat(), 'guests': 2}
    query.update(overrides)
    return query


def selection_query(selection, **overrides):
    """GET query of the booking page: the stay plus one room_<id> per chosen type."""
    query = stay(**overrides)
    query.update({f'room_{room_type.pk}': quantity for room_type, quantity in selection})
    return query


def form_data(selection, **overrides):
    """POST data of the booking form for `selection` = [(room_type, quantity)]."""
    data = {
        **GUEST,
        'adults': 2,
        'children': 0,
        'payment_option': 'PAY_AT_HOTEL',
        'idempotency_key': str(uuid.uuid4()),
        **stay(),
    }
    data.update({f'room_{room_type.pk}': quantity for room_type, quantity in selection})
    data.update(overrides)
    return data


def confirmed_url(prop, booking):
    return url('booking-confirmed', prop, booking_id=booking.pk)


def hold(room, age):
    """An unpaid FULL booking of `room` for days 10-13, created `age` ago."""
    booking = BookingFactory(
        property=room.property, status=Booking.Status.PENDING_PAYMENT,
        payment_option=Booking.PaymentOption.FULL,
        check_in_date=day(10), check_out_date=day(13),
    )
    BookingRoomFactory(booking=booking, room=room)
    Booking.objects.filter(pk=booking.pk).update(created_at=timezone.now() - age)
    return booking


class TestReadRoomQuantities:
    def test_reads_room_keys_and_skips_everything_else(self):
        data = {'room_3': '2', 'room_1': '1', 'room_2': '0', 'room_4': '', 'room_5': '  ', 'guests': '2', 'x': '9'}
        assert read_room_quantities(data) == {1: 1, 3: 2}

    @pytest.mark.parametrize('bad', ['abc', '-1', '1.5', '1e3', '٣', '99999', '0x2'])
    def test_bad_quantity_gives_a_thai_error_not_an_exception(self, bad):
        with pytest.raises(ValidationError) as exc:
            read_room_quantities({'room_1': bad})
        assert exc.value.messages == [INVALID_ROOMS_MESSAGE]

    @pytest.mark.parametrize('key', ['room_99999999999999999999', 'room_2147483648', 'room_' + '9' * 5000])
    def test_id_that_cannot_exist_gives_a_thai_error(self, key):
        with pytest.raises(ValidationError) as exc:
            read_room_quantities({key: '1'})
        assert exc.value.messages == [INVALID_ROOMS_MESSAGE]

    def test_zero_quantity_of_a_bad_id_is_still_skipped(self):
        assert read_room_quantities({'room_99999999999999999999': '0'}) == {}


class TestSearchPageWiring:
    def test_full_page_has_the_empty_book_form_and_results_have_no_form_of_their_own(self, client, hotel_a):
        make_room_type(hotel_a)

        content = client.get(url('search', hotel_a), stay()).content.decode()

        assert content.count('<form id="book-form" method="get"') == 1
        assert f'action="{url("book", hotel_a)}"' in content
        # The book form is empty: it is closed right after it opens
        assert re.search(r'<form id="book-form"[^>]*></form>', content)

    def test_htmx_partial_has_no_form_but_its_inputs_and_button_point_to_book_form(self, client, hotel_a):
        room_type = make_room_type(hotel_a)

        content = client.get(url('search', hotel_a), stay(), **HTMX).content.decode()

        assert '<form' not in content
        quantity_input = re.search(rf'<input[^>]*name="room_{room_type.pk}"[^>]*>', content).group(0)
        assert 'form="book-form"' in quantity_input and 'type="number"' in quantity_input
        assert 'min="0"' in quantity_input and 'value="0"' in quantity_input
        button = re.search(r'<button[^>]*>\s*จองห้องที่เลือก\s*</button>', content).group(0)
        assert 'type="submit"' in button and 'form="book-form"' in button and 'disabled' not in button
        assert content.count('จองห้องที่เลือก') == 1

    def test_hidden_fields_come_from_the_validated_search_not_the_raw_query(self, client, hotel_a):
        make_room_type(hotel_a)
        query = {'check_in': f' {day(10).isoformat()} ', 'check_out': f'{day(13).isoformat()} ', 'guests': ' 2 '}

        content = client.get(url('search', hotel_a), query, **HTMX).content.decode()

        for name, value in [('check_in', day(10).isoformat()), ('check_out', day(13).isoformat()), ('guests', '2')]:
            tag = re.search(rf'<input type="hidden"[^>]*name="{name}"[^>]*>', content).group(0)
            assert f'value="{value}"' in tag and 'form="book-form"' in tag

    def test_no_quantity_box_or_button_when_the_search_has_no_results(self, client, hotel_a):
        content = client.get(url('search', hotel_a), stay(), **HTMX).content.decode()
        assert 'จองห้องที่เลือก' not in content and 'name="room_' not in content

    def test_quantity_max_is_the_smaller_of_free_rooms_and_the_booking_limit(self, client, hotel_a):
        few = make_room_type(hotel_a, name='Few', rooms=3)
        many = make_room_type(hotel_a, name='Many', rooms=MAX_ROOMS_PER_BOOKING + 5)

        response = client.get(url('search', hotel_a), stay(), **HTMX)

        content = response.content.decode()
        assert re.search(rf'name="room_{few.pk}"[^>]*max="3"', content)
        assert re.search(rf'name="room_{many.pk}"[^>]*max="{MAX_ROOMS_PER_BOOKING}"', content)
        assert response.context['max_rooms_per_booking'] == MAX_ROOMS_PER_BOOKING
        assert f'{MAX_ROOMS_PER_BOOKING} ห้องต่อครั้ง' in content

    def test_search_form_to_book_page_round_trip_without_javascript(self, client, hotel_a):
        room_type = make_room_type(hotel_a)
        # What the browser submits for the book form: the hidden fields plus the quantity box
        query = {'check_in': day(10).isoformat(), 'check_out': day(13).isoformat(), 'guests': '2',
                 f'room_{room_type.pk}': '1'}

        response = client.get(url('book', hotel_a), query)

        assert response.status_code == 200
        assert response.context['lines'][0]['room_type'].pk == room_type.pk


class TestBookPageGet:
    def test_summary_shows_rooms_per_night_prices_estimate_times_and_nights(self, client, hotel_a):
        deluxe = make_room_type(
            hotel_a, rooms=2, prices=[Decimal('1000.00'), Decimal('1500.00'), Decimal('1200.50')],
        )

        response = client.get(url('book', hotel_a), selection_query([(deluxe, 2)]))

        content = response.content.decode()
        assert response.status_code == 200
        assert 'Deluxe × 2' in content
        # Per-night sum 1000 + 1500 + 1200.50 for one room; the estimate is that times 2
        assert '3,700.50' in content and '7,401.00' in content
        assert isinstance(response.context['estimated_total'], Decimal)
        assert response.context['estimated_total'] == Decimal('7401.00')
        assert '3 คืน' in content
        assert '14:00 น.' in content and '12:00 น.' in content
        assert 'Hotel A' in content

    def test_form_defaults_and_hidden_fields(self, client, hotel_a):
        deluxe = make_room_type(hotel_a, rooms=2)

        response = client.get(url('book', hotel_a), selection_query([(deluxe, 2)], guests=3))

        content = response.content.decode()
        form = response.context['form']
        assert form.initial['adults'] == 3 and form.initial['children'] == 0
        uuid.UUID(str(form['idempotency_key'].value()))
        assert f'name="room_{deluxe.pk}" value="2"' in content
        assert f'name="check_in" value="{day(10).isoformat()}"' in content
        assert f'name="check_out" value="{day(13).isoformat()}"' in content
        # The guest has to choose how to pay
        assert 'name="payment_option"' in content and 'checked' not in content
        assert 'csrfmiddlewaretoken' in content

    def test_each_visit_gets_its_own_idempotency_key(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)
        query = selection_query([(deluxe, 1)])
        first = client.get(url('book', hotel_a), query).context['form']['idempotency_key'].value()
        second = client.get(url('book', hotel_a), query).context['form']['idempotency_key'].value()
        assert first != second

    def test_several_room_types_on_one_page(self, client, hotel_a):
        deluxe = make_room_type(hotel_a, rooms=2)
        suite = make_room_type(hotel_a, name='Suite', prices=[Decimal('1500.00')] * 3)

        response = client.get(url('book', hotel_a), selection_query([(deluxe, 2), (suite, 1)]))

        # 2 x 3000 + 1 x 4500
        assert response.context['estimated_total'] == Decimal('10500.00')
        assert [line['quantity'] for line in response.context['lines']] == [2, 1]

    def test_it_never_creates_a_booking(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)
        client.get(url('book', hotel_a), selection_query([(deluxe, 1)]))
        assert not Booking.objects.exists() and not Guest.objects.exists()

    def test_unsupported_methods_are_405(self, client, hotel_a):
        assert client.put(url('book', hotel_a)).status_code == 405
        assert client.post(url('booking-confirmed', hotel_a, booking_id=uuid.uuid4())).status_code == 405


class TestBookPageGetRejections:
    def assert_rejected(self, response, message, hotel):
        assert response.status_code == 200
        assert message in response.context['error_messages']
        content = response.content.decode()
        assert message in content
        # No form, but a way back to the search
        assert 'name="first_name"' not in content
        assert response.context['back_url'].startswith(url('search', hotel))
        assert not Booking.objects.exists()

    def test_no_room_chosen(self, client, hotel_a):
        make_room_type(hotel_a)
        response = client.get(url('book', hotel_a), stay())
        self.assert_rejected(response, NO_ROOMS_MESSAGE, hotel_a)

    def test_only_zero_quantities(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)
        response = client.get(url('book', hotel_a), selection_query([(deluxe, 0)]))
        self.assert_rejected(response, NO_ROOMS_MESSAGE, hotel_a)

    @pytest.mark.parametrize('bad', ['abc', '-1', '1.5', 'x' * 50])
    def test_bad_quantity_is_a_thai_message_not_a_500(self, client, hotel_a, bad):
        deluxe = make_room_type(hotel_a)
        other = make_room_type(hotel_a, name='Other')
        query = selection_query([(other, 1)])
        query[f'room_{deluxe.pk}'] = bad
        self.assert_rejected(client.get(url('book', hotel_a), query), INVALID_ROOMS_MESSAGE, hotel_a)

    def test_empty_quantity_box_counts_as_zero(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)
        other = make_room_type(hotel_a, name='Other')
        query = selection_query([(other, 1)])
        query[f'room_{deluxe.pk}'] = ''
        response = client.get(url('book', hotel_a), query)
        assert response.context.get('error_messages') is None
        assert [line['room_type'].pk for line in response.context['lines']] == [other.pk]

    def test_id_too_large_for_the_database(self, client, hotel_a):
        make_room_type(hotel_a)
        query = {**stay(), 'room_99999999999999999999': '1'}
        self.assert_rejected(client.get(url('book', hotel_a), query), INVALID_ROOMS_MESSAGE, hotel_a)

    def test_more_than_the_limit_in_total(self, client, hotel_a):
        first = make_room_type(hotel_a, name='First', rooms=MAX_ROOMS_PER_BOOKING)
        second = make_room_type(hotel_a, name='Second', rooms=2)
        response = client.get(url('book', hotel_a), selection_query([(first, MAX_ROOMS_PER_BOOKING), (second, 1)]))
        self.assert_rejected(response, TOO_MANY_ROOMS_MESSAGE, hotel_a)

    def test_exactly_the_limit_is_accepted(self, client, hotel_a):
        many = make_room_type(hotel_a, rooms=MAX_ROOMS_PER_BOOKING)
        response = client.get(url('book', hotel_a), selection_query([(many, MAX_ROOMS_PER_BOOKING)]))
        assert response.context.get('error_messages') is None
        assert 'name="first_name"' in response.content.decode()

    def test_more_than_the_free_rooms(self, client, hotel_a):
        deluxe = make_room_type(hotel_a, rooms=2)
        response = client.get(url('book', hotel_a), selection_query([(deluxe, 3)]))
        self.assert_rejected(response, 'ห้องประเภท Deluxe เหลือ 2 ห้อง', hotel_a)

    def test_invalid_search_dates_or_guests(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)
        for bad in [stay(check_in=day(-2).isoformat()), stay(guests=0), stay(check_out='abc')]:
            query = {**bad, f'room_{deluxe.pk}': 1}
            response = client.get(url('book', hotel_a), query)
            assert response.status_code == 200
            assert response.context['error_messages']
            assert response.context['back_url'] == url('search', hotel_a)
        assert client.get(url('book', hotel_a)).status_code == 200


class TestBookPostPayAtHotel:
    def test_several_room_types_in_one_booking(self, client, hotel_a):
        deluxe = make_room_type(hotel_a, rooms=2)
        suite = make_room_type(hotel_a, name='Suite', prices=[Decimal('1500.00')] * 3)

        response = client.post(url('book', hotel_a), form_data([(deluxe, 2), (suite, 1)]))

        booking = Booking.objects.get()
        assert response.status_code == 302
        assert response['Location'] == confirmed_url(hotel_a, booking)
        assert booking.status == Booking.Status.CONFIRMED
        assert booking.payment_option == Booking.PaymentOption.PAY_AT_HOTEL
        assert booking.total_amount == Decimal('10500.00')
        assert BookingRoom.objects.filter(booking=booking).count() == 3
        assert {br.room_type_id for br in booking.booking_rooms.all()} == {deluxe.pk, suite.pk}
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == [GUEST['email']]

    def test_confirmation_page_shows_the_success_message_once(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)

        response = client.post(url('book', hotel_a), form_data([(deluxe, 1)]))
        first_visit = client.get(response['Location']).content.decode()
        refreshed = client.get(response['Location']).content.decode()

        assert EMAIL_SENT_MESSAGE in first_visit
        assert EMAIL_FAILED_MESSAGE not in first_visit
        # A refresh must not claim anything about the email
        assert EMAIL_LINE not in refreshed and 'ส่งอีเมล' not in refreshed
        assert 'กรุณาจดรหัสการจองไว้ใช้ตอนเช็คอิน' in refreshed

    def test_same_key_twice_makes_one_booking_one_email_and_no_message(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)
        data = form_data([(deluxe, 1)])

        first = client.post(url('book', hotel_a), data)
        client.get(first['Location'])  # the guest saw the page, so the message is used up
        second = client.post(url('book', hotel_a), data)
        page = client.get(second['Location']).content.decode()

        assert Booking.objects.count() == 1
        assert second.status_code == 302 and second['Location'] == first['Location']
        assert len(mail.outbox) == 1
        assert EMAIL_LINE not in page and EMAIL_FAILED_MESSAGE not in page

    def test_email_failure_still_books_and_shows_a_warning(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)

        with patch('apps.bookings.booking_views.send_booking_confirmation_safely', return_value=False) as send:
            response = client.post(url('book', hotel_a), form_data([(deluxe, 1)]))
        first_visit = client.get(response['Location']).content.decode()
        refreshed = client.get(response['Location']).content.decode()

        booking = Booking.objects.get()
        assert response.status_code == 302 and response['Location'] == confirmed_url(hotel_a, booking)
        send.assert_called_once_with(hotel_a, booking.pk)
        assert EMAIL_FAILED_MESSAGE in first_visit
        assert EMAIL_LINE not in first_visit and EMAIL_SENT_MESSAGE not in first_visit
        assert booking.status == Booking.Status.CONFIRMED
        # After the warning is gone the page says nothing about email, true or false
        assert 'ส่งอีเมล' not in refreshed

    def test_existing_guest_is_reused_without_overwriting(self, client, hotel_a):
        GuestFactory(property=hotel_a, email='somchai@example.com', first_name='Original', phone='0800000001')
        deluxe = make_room_type(hotel_a)

        client.post(url('book', hotel_a), form_data([(deluxe, 1)], email='SOMCHAI@example.com'))

        guest = Guest.objects.get()
        assert guest.first_name == 'Original' and guest.phone == '0800000001'


class TestBookPostFull:
    def test_goes_to_stripe_and_waits_for_payment(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)

        with patch('apps.bookings.booking_views.start_checkout', return_value=CHECKOUT_URL) as checkout:
            response = client.post(url('book', hotel_a), form_data([(deluxe, 1)], payment_option='FULL'))

        booking = Booking.objects.get()
        assert response.status_code == 302 and response['Location'] == CHECKOUT_URL
        assert booking.status == Booking.Status.PENDING_PAYMENT
        assert booking.payment_option == Booking.PaymentOption.FULL
        property_arg, booking_id, success_url, cancel_url = checkout.call_args.args
        assert property_arg == hotel_a and booking_id == booking.pk
        # Stripe brings the guest back to the payment result page, which shows the status
        assert success_url == cancel_url
        assert success_url.endswith(url('payment-result', hotel_a, booking_id=booking.pk))
        assert success_url.startswith('http')
        # The webhook sends the email after payment, not this page
        assert mail.outbox == []

    def test_stripe_error_shows_thai_message_with_502_and_keeps_the_booking(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)

        with patch('apps.bookings.booking_views.start_checkout', side_effect=stripe.StripeError('boom')):
            response = client.post(url('book', hotel_a), form_data([(deluxe, 1)], payment_option='FULL'))

        booking = Booking.objects.get()
        content = response.content.decode()
        assert response.status_code == 502
        assert 'ระบบชำระเงินขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง' in content
        assert booking.code in content
        assert booking.status == Booking.Status.PENDING_PAYMENT
        assert mail.outbox == []

    def test_checkout_validation_error_goes_to_the_payment_result_page(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)

        with patch('apps.bookings.booking_views.start_checkout', side_effect=ValidationError({'status': 'x'})):
            response = client.post(url('book', hotel_a), form_data([(deluxe, 1)], payment_option='FULL'))

        booking = Booking.objects.get()
        assert response.status_code == 302
        assert response['Location'].endswith(url('payment-result', hotel_a, booking_id=booking.pk))

    def test_resubmit_follows_the_stored_payment_option_not_the_form(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)
        key = str(uuid.uuid4())

        with patch('apps.bookings.booking_views.start_checkout', return_value=CHECKOUT_URL) as checkout:
            client.post(url('book', hotel_a), form_data([(deluxe, 1)], payment_option='FULL', idempotency_key=key))
            again = client.post(
                url('book', hotel_a), form_data([(deluxe, 1)], payment_option='PAY_AT_HOTEL', idempotency_key=key),
            )

        assert Booking.objects.count() == 1
        assert checkout.call_count == 2
        assert again.status_code == 302 and again['Location'] == CHECKOUT_URL
        assert Booking.objects.get().status == Booking.Status.PENDING_PAYMENT
        assert mail.outbox == []

    def test_resubmit_of_a_pay_at_hotel_key_as_full_stays_pay_at_hotel(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)
        key = str(uuid.uuid4())

        with patch('apps.bookings.booking_views.start_checkout') as checkout:
            first = client.post(url('book', hotel_a), form_data([(deluxe, 1)], idempotency_key=key))
            again = client.post(
                url('book', hotel_a), form_data([(deluxe, 1)], payment_option='FULL', idempotency_key=key),
            )

        booking = Booking.objects.get()
        assert again['Location'] == first['Location'] == confirmed_url(hotel_a, booking)
        checkout.assert_not_called()
        assert len(mail.outbox) == 1


class TestBookPostErrors:
    def test_room_taken_between_get_and_post_shows_the_service_message_and_no_form(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)
        data = form_data([(deluxe, 1)])
        assert client.get(url('book', hotel_a), selection_query([(deluxe, 1)])).status_code == 200
        taken = BookingFactory(property=hotel_a, check_in_date=day(10), check_out_date=day(13))
        BookingRoomFactory(booking=taken, room=deluxe.rooms.get())

        response = client.post(url('book', hotel_a), data)

        content = response.content.decode()
        assert response.status_code == 200
        assert any('เหลือไม่พอ' in message for message in response.context['error_messages'])
        assert 'เหลือไม่พอ' in content
        assert 'name="first_name"' not in content
        assert url('search', hotel_a) in response.context['back_url']
        assert Booking.objects.count() == 1  # only the one that took the room
        assert mail.outbox == []

    def test_over_capacity_is_an_error_on_the_adults_field(self, client, hotel_a):
        twin = make_room_type(hotel_a, name='Twin', max_occupancy=2)

        response = client.post(url('book', hotel_a), form_data([(twin, 1)], adults=3))

        assert response.status_code == 200
        assert 'เกินความจุ' in response.context['form'].errors['adults'][0]
        assert 'เกินความจุ' in response.content.decode()
        assert not Booking.objects.exists()

    @pytest.mark.parametrize('field, value', [
        ('email', 'not-an-email'),
        ('children', -1),
        ('adults', 0),
        ('first_name', ''),
        ('phone', '0' * 31),
        ('payment_option', 'BARTER'),
    ])
    def test_bad_input_is_a_field_error_in_thai_and_nothing_is_booked(self, client, hotel_a, field, value):
        deluxe = make_room_type(hotel_a)

        response = client.post(url('book', hotel_a), form_data([(deluxe, 1)], **{field: value}))

        errors = response.context['form'].errors
        assert response.status_code == 200
        assert list(errors) == [field]
        assert all(re.search('[฀-๿]', message) for message in errors[field])
        assert not Booking.objects.exists() and mail.outbox == []

    def test_missing_payment_option_is_an_error_on_that_field(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)
        data = form_data([(deluxe, 1)])
        del data['payment_option']

        response = client.post(url('book', hotel_a), data)

        assert response.context['form'].errors['payment_option'] == ['กรุณาเลือกวิธีชำระเงิน']
        assert not Booking.objects.exists()

    def test_invalid_form_is_shown_again_with_the_same_key_rooms_and_input(self, client, hotel_a):
        deluxe = make_room_type(hotel_a, rooms=2)
        key = str(uuid.uuid4())

        response = client.post(
            url('book', hotel_a), form_data([(deluxe, 2)], email='nope', idempotency_key=key),
        )

        content = response.content.decode()
        assert response.status_code == 200
        assert response.context['form']['idempotency_key'].value() == key
        assert f'name="room_{deluxe.pk}" value="2"' in content
        assert 'value="Somchai"' in content
        assert 'Deluxe × 2' in content

    @pytest.mark.parametrize('key', ['not-a-uuid', ''])
    def test_damaged_key_gets_a_fresh_one_and_does_not_book(self, client, hotel_a, key):
        deluxe = make_room_type(hotel_a)

        response = client.post(url('book', hotel_a), form_data([(deluxe, 1)], idempotency_key=key))

        form = response.context['form']
        assert response.status_code == 200
        assert STALE_FORM_MESSAGE in form.non_field_errors()
        new_key = form['idempotency_key'].value()
        assert new_key != key
        uuid.UUID(str(new_key))
        assert not Booking.objects.exists()

    def test_service_errors_go_to_known_fields_and_everything_else_is_non_field(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)
        error = ValidationError({'first_name': 'ชื่อซ้ำ', 'something_else': 'ข้อผิดพลาดอื่น'})

        with patch('apps.bookings.booking_views.get_or_create_booking', side_effect=error):
            response = client.post(url('book', hotel_a), form_data([(deluxe, 1)]))

        form = response.context['form']
        assert form.errors['first_name'] == ['ชื่อซ้ำ']
        assert form.non_field_errors() == ['ข้อผิดพลาดอื่น']
        assert 'ข้อผิดพลาดอื่น' in response.content.decode()

    def test_service_error_that_is_not_a_dict_is_a_non_field_error(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)

        with patch('apps.bookings.booking_views.get_or_create_booking', side_effect=ValidationError('พัง')):
            response = client.post(url('book', hotel_a), form_data([(deluxe, 1)]))

        assert response.context['form'].non_field_errors() == ['พัง']

    def test_more_than_the_limit_in_total_books_nothing(self, client, hotel_a):
        many = make_room_type(hotel_a, rooms=MAX_ROOMS_PER_BOOKING + 1)

        response = client.post(url('book', hotel_a), form_data([(many, MAX_ROOMS_PER_BOOKING + 1)], adults=1))

        assert response.status_code == 200
        assert TOO_MANY_ROOMS_MESSAGE in response.context['error_messages']
        assert TOO_MANY_ROOMS_MESSAGE in response.content.decode()
        assert not Booking.objects.exists()

    def test_no_room_in_the_post_books_nothing(self, client, hotel_a):
        make_room_type(hotel_a)

        response = client.post(url('book', hotel_a), form_data([]))

        assert response.status_code == 200
        assert NO_ROOMS_MESSAGE in response.context['error_messages']
        assert not Booking.objects.exists()

    def test_bad_quantity_in_the_post_books_nothing(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)

        response = client.post(url('book', hotel_a), form_data([(deluxe, 'abc')]))

        assert response.status_code == 200
        assert INVALID_ROOMS_MESSAGE in response.context['error_messages']
        assert not Booking.objects.exists()

    def test_stay_dates_that_went_bad_show_a_message_and_no_form(self, client, hotel_a):
        deluxe = make_room_type(hotel_a)

        response = client.post(url('book', hotel_a), form_data([(deluxe, 1)], check_in=day(-3).isoformat()))

        assert response.status_code == 200
        assert response.context['error_messages']
        assert 'name="first_name"' not in response.content.decode()
        assert not Booking.objects.exists()

    def test_csrf_token_is_required(self, hotel_a):
        deluxe = make_room_type(hotel_a)
        strict_client = Client(enforce_csrf_checks=True)

        response = strict_client.post(url('book', hotel_a), form_data([(deluxe, 1)]))

        assert response.status_code == 403
        assert not Booking.objects.exists()

    def test_csrf_passes_with_the_token_from_the_page(self, hotel_a):
        deluxe = make_room_type(hotel_a)
        strict_client = Client(enforce_csrf_checks=True)
        page = strict_client.get(url('book', hotel_a), selection_query([(deluxe, 1)]))
        token = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', page.content.decode()).group(1)

        response = strict_client.post(
            url('book', hotel_a), {**form_data([(deluxe, 1)]), 'csrfmiddlewaretoken': token},
        )

        assert response.status_code == 302
        assert Booking.objects.count() == 1


class TestExpiredHolds:
    def test_expired_unpaid_hold_is_listed_and_bookable_through_the_pages(self, client, hotel_a):
        twin = make_room_type(hotel_a, name='Twin')
        old = hold(twin.rooms.get(), age=PAYMENT_WINDOW + timedelta(minutes=1))

        search = client.get(url('search', hotel_a), stay())
        response = client.post(url('book', hotel_a), form_data([(twin, 1)]))

        old.refresh_from_db()
        assert 'Twin' in search.content.decode()
        assert response.status_code == 302
        assert old.status == Booking.Status.CANCELLED
        assert Booking.objects.filter(status=Booking.Status.CONFIRMED).count() == 1

    def test_hold_inside_the_window_is_hidden_and_not_bookable(self, client, hotel_a):
        twin = make_room_type(hotel_a, name='Twin')
        fresh = hold(twin.rooms.get(), age=PAYMENT_WINDOW - timedelta(minutes=1))

        search = client.get(url('search', hotel_a), stay())
        page = client.get(url('book', hotel_a), selection_query([(twin, 1)]))
        response = client.post(url('book', hotel_a), form_data([(twin, 1)]))

        fresh.refresh_from_db()
        assert 'Twin' not in search.content.decode()
        assert ROOM_UNAVAILABLE_MESSAGE in page.context['error_messages']
        assert response.status_code == 200
        assert fresh.status == Booking.Status.PENDING_PAYMENT
        assert Booking.objects.count() == 1


class TestConfirmedPage:
    def book_through_the_page(self, client, hotel, selection, **overrides):
        response = client.post(url('book', hotel), form_data(selection, **overrides))
        assert response.status_code == 302
        return response

    def test_shows_code_status_times_rooms_by_type_total_and_policy(self, client, hotel_a):
        hotel_a.policy_text = 'ห้ามสูบบุหรี่ในห้องพัก'
        hotel_a.save()
        deluxe = make_room_type(hotel_a, rooms=2)
        suite = make_room_type(hotel_a, name='Suite', prices=[Decimal('1500.00')] * 3)
        response = self.book_through_the_page(client, hotel_a, [(deluxe, 2), (suite, 1)], adults=3, children=1)
        booking = Booking.objects.get()

        page = client.get(response['Location'])

        content = page.content.decode()
        assert page.status_code == 200
        assert booking.code in content
        assert 'ยืนยันแล้ว' in content
        assert 'Hotel A' in content
        assert '14:00 น.' in content and '12:00 น.' in content
        assert '3 คืน' in content
        assert 'Deluxe × 2' in content and 'Suite × 1' in content
        assert 'ผู้ใหญ่ 3 คน' in content and 'เด็ก 1 คน' in content
        assert '10,500.00' in content
        assert 'ชำระที่โรงแรม' in content
        assert 'ห้ามสูบบุหรี่ในห้องพัก' in content
        # Real room numbers stay hidden until check-in
        assert 'ROOMNO' not in content

    def test_full_bookings_go_to_the_payment_result_page_in_every_status(self, client, hotel_a):
        for status in Booking.Status.values:
            booking = BookingFactory(
                property=hotel_a, payment_option=Booking.PaymentOption.FULL, status=status,
            )
            response = client.get(confirmed_url(hotel_a, booking))
            assert response.status_code == 302, status
            assert response['Location'] == url('payment-result', hotel_a, booking_id=booking.pk)

    def test_cancelled_booking_says_so_and_links_to_search(self, client, hotel_a):
        booking = BookingFactory(
            property=hotel_a, payment_option=Booking.PaymentOption.PAY_AT_HOTEL,
            status=Booking.Status.CANCELLED,
        )

        response = client.get(confirmed_url(hotel_a, booking))

        content = response.content.decode()
        assert response.status_code == 200
        assert 'การจองนี้ถูกยกเลิกแล้ว' in content
        assert 'หากต้องการเข้าพัก กรุณาทำการจองใหม่' in content
        assert url('search', hotel_a) in content
        assert 'จองห้องพักเรียบร้อย' not in content

    def test_unknown_booking_is_404(self, client, hotel_a):
        assert client.get(url('booking-confirmed', hotel_a, booking_id=uuid.uuid4())).status_code == 404

    def test_never_shows_the_guest_of_the_booking(self, client, hotel_a):
        """Anyone may type someone else's email, so the page must not reveal the profile it reuses."""
        GuestFactory(
            property=hotel_a, email='owner@example.com', first_name='Original', last_name='Owner', phone='0899999999',
        )
        deluxe = make_room_type(hotel_a)

        response = client.post(
            url('book', hotel_a),
            form_data([(deluxe, 1)], email='Owner@Example.com', first_name='Typed', last_name='Stranger', phone='0811111111'),
            follow=True,
        )

        content = response.content.decode()
        assert response.status_code == 200 and Booking.objects.get().guest.first_name == 'Original'
        for secret in ['Original', 'Owner', 'owner@example.com', '0899999999', 'Typed', 'Stranger', '0811111111']:
            assert secret.lower() not in content.lower(), secret

    def test_query_count_does_not_depend_on_the_number_of_rooms(self, client, hotel_a):
        deluxe = make_room_type(hotel_a, rooms=4)
        rooms = list(deluxe.rooms.order_by('number'))
        one = BookingFactory(property=hotel_a, payment_option=Booking.PaymentOption.PAY_AT_HOTEL)
        BookingRoomFactory(booking=one, room=rooms[0])
        three = BookingFactory(property=hotel_a, payment_option=Booking.PaymentOption.PAY_AT_HOTEL)
        for room in rooms[1:]:
            BookingRoomFactory(booking=three, room=room)

        def count_queries(booking):
            with CaptureQueriesContext(connection) as queries:
                assert client.get(confirmed_url(hotel_a, booking)).status_code == 200
            return len(queries)

        count_queries(one)  # warm-up
        assert count_queries(three) == count_queries(one)


class TestIsolation:
    @pytest.fixture
    def beta(self, hotel_b):
        return make_room_type(hotel_b, name='Beta Suite')

    def test_get_with_another_hotels_room_type_gets_the_neutral_message(self, client, hotel_a, beta):
        make_room_type(hotel_a)

        response = client.get(url('book', hotel_a), selection_query([(beta, 1)]))

        assert response.status_code == 200
        assert response.context['error_messages'] == [ROOM_UNAVAILABLE_MESSAGE]
        assert 'Beta Suite' not in response.content.decode()
        assert 'Hotel B' not in response.content.decode()
        assert not Booking.objects.exists()

    def test_other_hotel_missing_and_sold_out_look_exactly_the_same(self, client, hotel_a, beta):
        sold_out = make_room_type(hotel_a, name='Sold out')
        taken = BookingFactory(property=hotel_a, check_in_date=day(10), check_out_date=day(13))
        BookingRoomFactory(booking=taken, room=sold_out.rooms.get())

        pages = []
        for room_type_id in [beta.pk, 999_999, sold_out.pk]:
            query = {**stay(), f'room_{room_type_id}': 1}
            response = client.get(url('book', hotel_a), query)
            pages.append((response.status_code, response.context['error_messages']))

        assert pages == [(200, [ROOM_UNAVAILABLE_MESSAGE])] * 3

    def test_post_with_another_hotels_room_type_is_404_and_changes_nothing(self, client, hotel_a, hotel_b, beta):
        make_room_type(hotel_a)

        response = client.post(url('book', hotel_a), form_data([(beta, 1)]))

        assert response.status_code == 404
        assert 'Beta Suite' not in response.content.decode()
        assert not Booking.objects.exists()  # in neither hotel
        assert not BookingRoom.objects.exists() and not Guest.objects.exists()
        assert mail.outbox == []

    def test_post_with_another_hotels_room_type_next_to_a_valid_one_books_nothing(self, client, hotel_a, beta):
        deluxe = make_room_type(hotel_a)

        response = client.post(url('book', hotel_a), form_data([(deluxe, 1), (beta, 1)]))

        assert response.status_code == 404
        assert not Booking.objects.exists() and not BookingRoom.objects.exists()

    def test_invalid_post_with_another_hotels_room_type_leaks_nothing(self, client, hotel_a, beta):
        response = client.post(url('book', hotel_a), form_data([(beta, 1)], email='nope'))

        assert response.status_code == 200
        assert 'Beta Suite' not in response.content.decode()
        assert response.context['error_messages'] == [ROOM_UNAVAILABLE_MESSAGE]

    def test_idempotency_key_of_another_hotel_does_not_return_its_booking(self, client, hotel_a, hotel_b):
        deluxe = make_room_type(hotel_a)
        foreign = BookingFactory(property=hotel_b, idempotency_key=uuid.uuid4())

        response = client.post(
            url('book', hotel_a), form_data([(deluxe, 1)], idempotency_key=str(foreign.idempotency_key)),
        )

        mine = Booking.objects.get(property=hotel_a)
        assert response['Location'] == confirmed_url(hotel_a, mine)
        assert mine.pk != foreign.pk

    def test_confirmation_page_of_another_hotels_booking_is_404(self, client, hotel_a, hotel_b):
        booking = BookingFactory(property=hotel_b, payment_option=Booking.PaymentOption.PAY_AT_HOTEL)

        assert client.get(confirmed_url(hotel_a, booking)).status_code == 404
        assert client.get(confirmed_url(hotel_b, booking)).status_code == 200

    def test_unknown_or_inactive_property_is_404(self, client, assert_404_all_methods):
        PropertyFactory(slug='closed', is_active=False)
        for slug in ['no-such-hotel', 'closed']:
            assert_404_all_methods(f'/p/{slug}/book/')
            assert_404_all_methods(f'/p/{slug}/bookings/{uuid.uuid4()}/confirmed/')

    def test_pages_are_open_to_guests_and_to_staff_of_other_hotels(self, client, login_member, hotel_a, hotel_b):
        deluxe = make_room_type(hotel_a)
        login_member(hotel_b)

        assert client.get(url('book', hotel_a), selection_query([(deluxe, 1)])).status_code == 200
        response = client.post(url('book', hotel_a), form_data([(deluxe, 1)]))
        assert response.status_code == 302
        assert Booking.objects.get().property == hotel_a
