from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.properties.tests.factories import PropertyFactory
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


def day(offset):
    return timezone.localdate() + timedelta(days=offset)


def api_url(prop):
    return reverse('bookings:api-availability', kwargs={'property_slug': prop.slug})


def params(check_in=10, check_out=13, **extra):
    return {'check_in': day(check_in).isoformat(), 'check_out': day(check_out).isoformat(), **extra}


def make_room_type(prop, rooms=1, prices=(Decimal('1000.00'),) * 3, start=10, **kwargs):
    room_type = RoomTypeFactory(property=prop, **kwargs)
    for _ in range(rooms):
        RoomFactory(property=prop, room_type=room_type)
    for offset, price in enumerate(prices):
        DailyRateFactory(property=prop, room_type=room_type, date=day(start + offset), price=price)
    return room_type


def names(response):
    return [rt['name'] for rt in response.json()['room_types']]


class TestAvailabilityAPI:
    def test_returns_room_types_with_details_and_per_night_total(self, client, hotel_a):
        room_type = make_room_type(
            hotel_a, rooms=2, name='Deluxe', bed_type='King', size_sqm=32, max_occupancy=3,
            prices=[Decimal('1000.00'), Decimal('1500.00'), Decimal('1200.50')],
        )
        RoomTypeAmenityFactory(
            property=hotel_a, room_type=room_type, amenity=AmenityFactory(property=hotel_a, name='Wi-Fi'),
        )
        image = RoomTypeImageFactory(property=hotel_a, room_type=room_type)

        response = client.get(api_url(hotel_a), params(guests=2))

        assert response.status_code == 200
        assert response.json() == {
            'check_in': day(10).isoformat(),
            'check_out': day(13).isoformat(),
            'nights': 3,
            'guests': 2,
            'room_types': [{
                'id': room_type.pk,
                'name': 'Deluxe',
                'description': room_type.description,
                'max_occupancy': 3,
                'bed_type': 'King',
                'size_sqm': 32,
                'amenities': ['Wi-Fi'],
                'images': [image.image.url],
                'available_count': 2,
                'total_price': '3700.50',  # per-night sum, as a decimal string
            }],
        }

    def test_money_is_a_string_never_a_float(self, client, hotel_a):
        make_room_type(hotel_a, prices=[Decimal('0.10'), Decimal('0.20'), Decimal('0.30')])
        total = client.get(api_url(hotel_a), params()).json()['room_types'][0]['total_price']
        assert total == '0.60'
        assert isinstance(total, str)

    def test_guests_is_optional(self, client, hotel_a):
        make_room_type(hotel_a, name='Deluxe', max_occupancy=1)
        response = client.get(api_url(hotel_a), params())
        assert response.json()['guests'] is None
        assert names(response) == ['Deluxe']

    def test_group_can_be_split_across_rooms_of_one_type(self, client, hotel_a):
        make_room_type(hotel_a, rooms=2, name='Twin', max_occupancy=2)
        assert names(client.get(api_url(hotel_a), params(guests=4))) == ['Twin']
        assert names(client.get(api_url(hotel_a), params(guests=5))) == []

    def test_booked_rooms_reduce_capacity(self, client, hotel_a):
        room_type = make_room_type(hotel_a, rooms=2, name='Twin', max_occupancy=2)
        booking = BookingFactory(property=hotel_a, check_in_date=day(10), check_out_date=day(13))
        BookingRoomFactory(booking=booking, room=room_type.rooms.first())

        response = client.get(api_url(hotel_a), params(guests=3))

        assert names(response) == []

    def test_no_availability_is_an_empty_list(self, client, hotel_a):
        assert client.get(api_url(hotel_a), params()).json()['room_types'] == []

    def test_query_count_does_not_grow_with_results(self, client, hotel_a):
        def count_queries():
            with CaptureQueriesContext(connection) as ctx:
                assert client.get(api_url(hotel_a), params()).status_code == 200
            return len(ctx.captured_queries)

        room_type = make_room_type(hotel_a, name='Type 0')
        RoomTypeImageFactory(property=hotel_a, room_type=room_type)
        baseline = count_queries()

        for i in range(1, 5):
            room_type = make_room_type(hotel_a, rooms=2, name=f'Type {i}')
            RoomTypeImageFactory(property=hotel_a, room_type=room_type)
            RoomTypeAmenityFactory(property=hotel_a, room_type=room_type)

        assert count_queries() == baseline


class TestValidation:
    def test_missing_dates_is_400_in_thai(self, client, hotel_a):
        response = client.get(api_url(hotel_a))
        assert response.status_code == 400
        assert response.json() == {'check_in': ['กรุณาระบุวันที่'], 'check_out': ['กรุณาระบุวันที่']}

    def test_malformed_date_is_400_in_thai(self, client, hotel_a):
        response = client.get(api_url(hotel_a), {'check_in': '2026-13-45', 'check_out': 'x'})
        assert response.status_code == 400
        assert response.json()['check_in'] == ['รูปแบบวันที่ไม่ถูกต้อง (ใช้ YYYY-MM-DD)']

    @pytest.mark.parametrize('guests, message', [
        (0, 'จำนวนผู้เข้าพักต้องมีอย่างน้อย 1 คน'),
        (-3, 'จำนวนผู้เข้าพักต้องมีอย่างน้อย 1 คน'),
        ('two', 'จำนวนผู้เข้าพักต้องเป็นตัวเลข'),
    ])
    def test_invalid_guests_is_400_in_thai(self, client, hotel_a, guests, message):
        response = client.get(api_url(hotel_a), params(guests=guests))
        assert response.status_code == 400
        assert response.json() == {'guests': [message]}

    def test_check_out_not_after_check_in_is_400_from_service(self, client, hotel_a):
        response = client.get(api_url(hotel_a), params(check_in=12, check_out=12))
        assert response.status_code == 400
        assert response.json() == {'check_out': ['วันเช็คเอาต์ต้องอยู่หลังวันเช็คอิน']}

    def test_past_check_in_is_400(self, client, hotel_a):
        response = client.get(api_url(hotel_a), params(check_in=-1, check_out=2))
        assert response.status_code == 400
        assert 'check_in' in response.json()

    @pytest.mark.parametrize('method', ['post', 'put', 'patch', 'delete'])
    def test_only_get_is_allowed(self, client, hotel_a, method):
        assert getattr(client, method)(api_url(hotel_a)).status_code == 405


class TestAccessAndIsolation:
    def test_anonymous_guest_can_search(self, client, hotel_a):
        make_room_type(hotel_a, name='Deluxe')
        assert names(client.get(api_url(hotel_a), params())) == ['Deluxe']

    def test_member_of_other_hotel_sees_only_the_visited_hotel(self, client, login_member, hotel_a, hotel_b):
        login_member(hotel_b)
        make_room_type(hotel_a, name='A Room')
        make_room_type(hotel_b, name='B Room')
        assert names(client.get(api_url(hotel_a), params())) == ['A Room']

    def test_other_hotels_rates_bookings_amenities_and_images_never_leak(self, client, hotel_a, hotel_b):
        mine = make_room_type(hotel_a, name='Same', prices=[Decimal('1000.00')] * 3)
        theirs = make_room_type(hotel_b, name='Same', prices=[Decimal('9999.00')] * 3)
        RoomTypeAmenityFactory(
            property=hotel_b, room_type=theirs, amenity=AmenityFactory(property=hotel_b, name='B Spa'),
        )
        RoomTypeImageFactory(property=hotel_b, room_type=theirs)
        booking = BookingFactory(property=hotel_b, check_in_date=day(10), check_out_date=day(13))
        BookingRoomFactory(booking=booking, room=theirs.rooms.get())

        [result] = client.get(api_url(hotel_a), params()).json()['room_types']

        assert result['id'] == mine.pk
        assert result['total_price'] == '3000.00'
        assert result['available_count'] == 1
        assert result['amenities'] == []
        assert result['images'] == []

    def test_unknown_or_inactive_property_is_404(self, client):
        inactive = PropertyFactory(slug='closed', is_active=False)
        make_room_type(inactive, name='Hidden')
        assert client.get('/api/p/no-such-hotel/availability/', params()).status_code == 404
        assert client.get(api_url(inactive), params()).status_code == 404
