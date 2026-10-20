import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from apps.properties.tests.factories import PropertyFactory, UserFactory
from apps.rooms.tests.factories import (
    AmenityFactory,
    DailyRateFactory,
    RoomFactory,
    RoomTypeAmenityFactory,
    RoomTypeFactory,
    RoomTypeImageFactory,
)

from .factories import BookingFactory, BookingRoomFactory

pytestmark = pytest.mark.django_db

HTMX = {'HTTP_HX_REQUEST': 'true'}
NOT_SEARCHED_HINT = 'ไม่มีห้องว่างตรงกับเงื่อนไขที่ค้นหา'
THAI = re.compile('[฀-๿]')


def day(offset):
    return timezone.localdate() + timedelta(days=offset)


def search_url(prop):
    return reverse('bookings:search', kwargs={'property_slug': prop.slug})


def params(check_in=10, check_out=13, guests=2):
    return {'check_in': day(check_in).isoformat(), 'check_out': day(check_out).isoformat(), 'guests': guests}


def make_room_type(prop, prices=(Decimal('1000.00'),) * 3, start=10, **kwargs):
    room_type = RoomTypeFactory(property=prop, **kwargs)
    RoomFactory(property=prop, room_type=room_type)
    for offset, price in enumerate(prices):
        DailyRateFactory(property=prop, room_type=room_type, date=day(start + offset), price=price)
    return room_type


class TestSearchPage:
    def test_first_visit_shows_neither_results_nor_errors(self, client, hotel_a):
        make_room_type(hotel_a, name='Deluxe')

        response = client.get(search_url(hotel_a))

        assert response.status_code == 200
        content = response.content.decode()
        assert response.context['room_types'] is None
        assert not response.context['form'].errors
        assert NOT_SEARCHED_HINT not in content
        assert 'Deluxe' not in content
        assert 'rr-field__error' not in content

    def test_header_shows_property_name_and_policy(self, client):
        prop = PropertyFactory(name='Sea View', slug='sea-view', policy_text='ห้ามสูบบุหรี่')
        response = client.get(search_url(prop))
        content = response.content.decode()
        assert 'Sea View' in content
        assert 'ห้ามสูบบุหรี่' in content

    def test_results_card_shows_room_details_and_per_night_total(self, client, hotel_a):
        room_type = make_room_type(
            hotel_a, name='Deluxe', bed_type='King', size_sqm=32, max_occupancy=3,
            prices=[Decimal('1000.00'), Decimal('1500.00'), Decimal('1200.50')],
        )
        RoomTypeAmenityFactory(property=hotel_a, room_type=room_type, amenity=AmenityFactory(property=hotel_a, name='Wi-Fi'))
        RoomTypeImageFactory(property=hotel_a, room_type=room_type)

        response = client.get(search_url(hotel_a), params())

        content = response.content.decode()
        assert response.status_code == 200
        assert 'Deluxe' in content and 'King' in content and '32' in content
        assert 'Wi-Fi' in content
        assert 'เหลือ 1 ห้อง' in content
        # Per-night sum 1000 + 1500 + 1200.50, not rate x nights
        assert '3,700.50' in content
        assert '<img class="rr-room-card__image"' in content
        # One quantity box per card and one active submit button; the old disabled button is gone
        assert f'name="room_{room_type.pk}"' in content
        assert 'จองห้องที่เลือก' in content
        assert 'disabled' not in content and 'เลือกห้องนี้' not in content
        assert isinstance(response.context['room_types'][0].total_price, Decimal)

    def test_form_is_prefilled_after_search(self, client, hotel_a):
        response = client.get(search_url(hotel_a), params(guests=3))
        content = response.content.decode()
        assert f'value="{day(10).isoformat()}"' in content
        assert 'value="3"' in content

    def test_no_availability_shows_thai_empty_message(self, client, hotel_a):
        room_type = make_room_type(hotel_a, name='Deluxe')
        booking = BookingFactory(property=hotel_a, check_in_date=day(10), check_out_date=day(13))
        BookingRoomFactory(booking=booking, room=room_type.rooms.get())

        response = client.get(search_url(hotel_a), params())

        assert response.status_code == 200
        assert response.context['room_types'] == []
        assert NOT_SEARCHED_HINT in response.content.decode()

    def test_room_type_without_full_rates_is_not_shown(self, client, hotel_a):
        make_room_type(hotel_a, name='Partial', prices=[Decimal('1000.00')] * 2)
        response = client.get(search_url(hotel_a), params())
        assert 'Partial' not in response.content.decode()
        assert NOT_SEARCHED_HINT in response.content.decode()

    def test_room_type_without_image_renders_no_img(self, client, hotel_a):
        make_room_type(hotel_a, name='Deluxe')
        response = client.get(search_url(hotel_a), params())
        content = response.content.decode()
        assert 'Deluxe' in content
        assert '<img' not in content

    def test_first_image_is_the_earliest_uploaded(self, client, hotel_a):
        room_type = make_room_type(hotel_a, name='Deluxe')
        first = RoomTypeImageFactory(property=hotel_a, room_type=room_type)
        RoomTypeImageFactory(property=hotel_a, room_type=room_type)

        response = client.get(search_url(hotel_a), params())

        assert first.image.url in response.content.decode()

    @pytest.mark.parametrize('guests', [5, 10**12])
    def test_guests_over_every_capacity_gives_empty_result(self, client, hotel_a, guests):
        make_room_type(hotel_a, name='Deluxe', max_occupancy=2)

        response = client.get(search_url(hotel_a), params(guests=guests))

        assert response.status_code == 200
        assert response.context['room_types'] == []
        assert NOT_SEARCHED_HINT in response.content.decode()

    def test_guests_filter_keeps_only_big_enough_types(self, client, hotel_a):
        make_room_type(hotel_a, name='Small', max_occupancy=2)
        make_room_type(hotel_a, name='Family', max_occupancy=4)

        content = client.get(search_url(hotel_a), params(guests=3)).content.decode()

        assert 'Family' in content
        assert 'Small' not in content

    @pytest.mark.parametrize('bad', ['32/13/2026', 'abc', ''])
    def test_malformed_date_shows_thai_error_not_500(self, client, hotel_a, bad):
        query = {**params(), 'check_in': bad}

        response = client.get(search_url(hotel_a), query)

        assert response.status_code == 200
        assert 'check_in' in response.context['form'].errors
        assert 'rr-field__error' in response.content.decode()
        assert response.context['room_types'] is None

    def test_date_rules_come_from_the_service_and_attach_to_fields(self, client, hotel_a):
        response = client.get(search_url(hotel_a), params(check_in=-2, check_out=-3))

        errors = response.context['form'].errors
        assert errors['check_in'] == ['วันเช็คอินต้องไม่ใช่วันที่ผ่านมาแล้ว']
        assert errors['check_out'] == ['วันเช็คเอาต์ต้องอยู่หลังวันเช็คอิน']

    @pytest.mark.parametrize('guests', [0, -1, 'x'])
    def test_invalid_guests_is_a_field_error(self, client, hotel_a, guests):
        response = client.get(search_url(hotel_a), params(guests=guests))
        assert response.status_code == 200
        assert 'guests' in response.context['form'].errors

    @pytest.mark.parametrize('check_in, check_out', [(10, 10), (13, 10)])
    def test_check_out_not_after_check_in_shows_thai_error_and_no_cards(self, client, hotel_a, check_in, check_out):
        make_room_type(hotel_a, name='Deluxe')

        response = client.get(search_url(hotel_a), params(check_in=check_in, check_out=check_out))

        assert response.status_code == 200
        assert response.context['form'].errors['check_out'] == ['วันเช็คเอาต์ต้องอยู่หลังวันเช็คอิน']
        assert 'วันเช็คเอาต์ต้องอยู่หลังวันเช็คอิน' in response.content.decode()
        assert response.context['room_types'] is None
        assert 'Deluxe' not in response.content.decode()

    def test_past_check_in_alone_shows_thai_error_and_no_cards(self, client, hotel_a):
        # Rates exist for the past nights, so only the form can be what blocks the search
        make_room_type(hotel_a, name='Deluxe', prices=[Decimal('1000.00')] * 3, start=-2)

        response = client.get(search_url(hotel_a), params(check_in=-2, check_out=1))

        errors = response.context['form'].errors
        assert response.status_code == 200
        assert errors['check_in'] == ['วันเช็คอินต้องไม่ใช่วันที่ผ่านมาแล้ว']
        assert 'check_out' not in errors
        assert 'วันเช็คอินต้องไม่ใช่วันที่ผ่านมาแล้ว' in response.content.decode()
        assert response.context['room_types'] is None
        assert 'Deluxe' not in response.content.decode()

    @pytest.mark.parametrize('bad', ['2026-02-30', 'abc', ''])
    def test_malformed_check_out_shows_thai_error_not_500(self, client, hotel_a, bad):
        response = client.get(search_url(hotel_a), {**params(), 'check_out': bad})

        assert response.status_code == 200
        messages = response.context['form'].errors['check_out']
        assert messages and all(THAI.search(message) for message in messages)
        assert escape(messages[0]) in response.content.decode()
        assert response.context['room_types'] is None

    @pytest.mark.parametrize('guests', [0, -1, 'x'])
    def test_invalid_guests_error_message_is_thai(self, client, hotel_a, guests):
        response = client.get(search_url(hotel_a), params(guests=guests))

        assert response.status_code == 200
        messages = response.context['form'].errors['guests']
        assert messages and all(THAI.search(message) for message in messages)
        assert escape(messages[0]) in response.content.decode()
        assert response.context['room_types'] is None

    def test_prefetch_keeps_query_count_flat(self, client, hotel_a):
        def count_queries():
            with CaptureQueriesContext(connection) as queries:
                assert client.get(search_url(hotel_a), params()).status_code == 200
            return len(queries)

        make_room_type(hotel_a, name='T0')
        baseline = count_queries()
        for index in range(1, 4):
            room_type = make_room_type(hotel_a, name=f'T{index}')
            RoomTypeAmenityFactory(
                property=hotel_a, room_type=room_type, amenity=AmenityFactory(property=hotel_a),
            )
            RoomTypeImageFactory(property=hotel_a, room_type=room_type)

        assert count_queries() == baseline

    def test_post_is_not_allowed(self, client, hotel_a):
        assert client.post(search_url(hotel_a), params()).status_code == 405


class TestHtmx:
    def test_htmx_request_renders_only_the_partial(self, client, hotel_a):
        make_room_type(hotel_a, name='Deluxe')

        response = client.get(search_url(hotel_a), params(), **HTMX)

        content = response.content.decode()
        assert 'Deluxe' in content
        assert '<html' not in content and '<form' not in content
        assert 'HX-Request' in response['Vary']

    def test_normal_request_renders_full_page(self, client, hotel_a):
        content = client.get(search_url(hotel_a), params()).content.decode()
        assert '<html' in content
        assert 'hx-target="#results"' in content
        assert 'method="get"' in content

    def test_htmx_invalid_form_returns_error_summary(self, client, hotel_a):
        response = client.get(search_url(hotel_a), params(check_in=-2, check_out=-3), **HTMX)

        content = response.content.decode()
        assert response.status_code == 200
        assert 'วันเช็คอินต้องไม่ใช่วันที่ผ่านมาแล้ว' in content
        assert 'วันเช็คเอาต์ต้องอยู่หลังวันเช็คอิน' in content

    def test_full_page_invalid_form_does_not_duplicate_errors(self, client, hotel_a):
        content = client.get(search_url(hotel_a), params(check_in=-2, check_out=-3)).content.decode()
        assert content.count('วันเช็คอินต้องไม่ใช่วันที่ผ่านมาแล้ว') == 1

    def test_htmx_first_visit_is_empty(self, client, hotel_a):
        response = client.get(search_url(hotel_a), **HTMX)
        assert response.content.decode().strip() == ''


class TestAccess:
    def test_anonymous_guest_can_search(self, client, hotel_a):
        response = client.get(search_url(hotel_a), params())
        assert response.status_code == 200

    def test_logged_in_non_member_can_search(self, client, hotel_a):
        client.force_login(UserFactory())
        assert client.get(search_url(hotel_a), params()).status_code == 200

    def test_unknown_or_inactive_property_is_404(self, client, assert_404_all_methods):
        PropertyFactory(slug='closed', is_active=False)
        assert_404_all_methods('/p/no-such-hotel/search/')
        assert_404_all_methods('/p/closed/search/')


class TestIsolation:
    def test_other_hotels_room_types_never_appear(self, client, hotel_a, hotel_b):
        make_room_type(hotel_a, name='Alpha Suite')
        beta = make_room_type(hotel_b, name='Beta Suite')
        RoomTypeAmenityFactory(property=hotel_b, room_type=beta, amenity=AmenityFactory(property=hotel_b, name='Beta Spa'))
        RoomTypeImageFactory(property=hotel_b, room_type=beta)

        content_a = client.get(search_url(hotel_a), params()).content.decode()
        content_b = client.get(search_url(hotel_b), params()).content.decode()

        assert 'Alpha Suite' in content_a
        assert 'Beta Suite' not in content_a and 'Beta Spa' not in content_a
        assert f'properties/{hotel_b.id}/' not in content_a
        assert 'Beta Suite' in content_b
        assert 'Alpha Suite' not in content_b

    def test_other_hotels_name_and_policy_never_appear(self, client, hotel_a, hotel_b):
        hotel_a.policy_text = 'นโยบายของ A'
        hotel_a.save()
        hotel_b.policy_text = 'นโยบายลับของ B'
        hotel_b.save()
        make_room_type(hotel_a, name='Alpha Suite')
        make_room_type(hotel_b, name='Beta Suite')

        content_a = client.get(search_url(hotel_a), params()).content.decode()

        assert 'Hotel A' in content_a and 'นโยบายของ A' in content_a and 'Alpha Suite' in content_a
        assert 'Hotel B' not in content_a
        assert 'นโยบายลับของ B' not in content_a
        assert 'Beta Suite' not in content_a

    def test_other_hotels_rates_and_bookings_do_not_affect_results(self, client, hotel_a, hotel_b):
        make_room_type(hotel_a, name='Alpha', prices=[Decimal('1000.00')] * 3)
        # Same dates, same room type name and a booking in hotel B must not leak into A
        beta = make_room_type(hotel_b, name='Alpha', prices=[Decimal('9999.00')] * 3)
        booking = BookingFactory(property=hotel_b, check_in_date=day(10), check_out_date=day(13))
        BookingRoomFactory(booking=booking, room=beta.rooms.get())

        content = client.get(search_url(hotel_a), params()).content.decode()

        assert '3,000.00' in content
        assert '29,997.00' not in content
        assert 'เหลือ 1 ห้อง' in content

    def test_member_of_other_hotel_sees_only_the_visited_hotel(self, client, login_member, hotel_a, hotel_b):
        make_room_type(hotel_a, name='Alpha Suite')
        make_room_type(hotel_b, name='Beta Suite')
        login_member(hotel_b)

        content = client.get(search_url(hotel_a), params()).content.decode()

        assert 'Alpha Suite' in content
        assert 'Beta Suite' not in content
