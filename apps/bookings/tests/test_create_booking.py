from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.http import Http404
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.bookings import services
from apps.bookings.models import Booking, BookingNightRate, BookingRoom, Guest
from apps.bookings.services import ROOM_TAKEN_MESSAGE, create_booking, get_or_create_booking
from apps.rooms.models import DailyRate
from apps.rooms.tests.factories import DailyRateFactory, RoomFactory, RoomTypeFactory

from .factories import BookingFactory, BookingRoomFactory

pytestmark = pytest.mark.django_db

GUEST = {'first_name': 'Somchai', 'last_name': 'Jaidee', 'email': 'somchai@example.com', 'phone': '0812345678'}


def day(offset):
    return timezone.localdate() + timedelta(days=offset)


def make_room_type(prop, rooms=1, prices=(Decimal('1000.00'),) * 2, start=10, **kwargs):
    room_type = RoomTypeFactory(property=prop, **kwargs)
    for i in range(rooms):
        RoomFactory(property=prop, room_type=room_type, number=f'{room_type.name}-{i + 1:02d}')
    for offset, price in enumerate(prices):
        DailyRateFactory(property=prop, room_type=room_type, date=day(start + offset), price=price)
    return room_type


def book(prop, rooms, check_in=10, check_out=12, adults=2, children=0, payment_option='FULL', guest=GUEST):
    return create_booking(
        prop,
        check_in=day(check_in),
        check_out=day(check_out),
        rooms=[{'room_type_id': rt.pk, 'quantity': qty} for rt, qty in rooms],
        guest=guest,
        adults=adults,
        children=children,
        payment_option=payment_option,
    )


def assert_nothing_created():
    assert not Guest.objects.exists()
    assert not Booking.objects.exists()
    assert not BookingRoom.objects.exists()
    assert not BookingNightRate.objects.exists()


class TestCreateBooking:
    def test_books_several_rooms_on_one_bill_with_per_night_prices(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=3, name='Twin', prices=[Decimal('1000.00'), Decimal('1500.00')])
        suite = make_room_type(hotel_a, rooms=1, name='Suite', prices=[Decimal('3000.00'), Decimal('3200.50')])

        booking = book(hotel_a, [(twin, 2), (suite, 1)], adults=4, children=1)

        assert booking.status == Booking.Status.PENDING_PAYMENT
        assert booking.guest.email == 'somchai@example.com'
        # 2 x (1000 + 1500) + (3000 + 3200.50)
        assert booking.total_amount == Decimal('11200.50')

        rooms = list(booking.booking_rooms.select_related('room').order_by('room__number'))
        assert [r.room.number for r in rooms] == ['Suite-01', 'Twin-01', 'Twin-02']
        assert [r.subtotal for r in rooms] == [Decimal('6200.50'), Decimal('2500.00'), Decimal('2500.00')]
        assert all(r.stay_range.lower == day(10) and r.stay_range.upper == day(12) for r in rooms)

        twin_nights = rooms[1].night_rates.order_by('night')
        assert [(n.night, n.price) for n in twin_nights] == [
            (day(10), Decimal('1000.00')), (day(11), Decimal('1500.00')),
        ]
        assert BookingNightRate.objects.count() == 3 * 2

    def test_pay_at_hotel_is_confirmed_immediately(self, hotel_a):
        twin = make_room_type(hotel_a, name='Twin')
        assert book(hotel_a, [(twin, 1)], payment_option='PAY_AT_HOTEL').status == Booking.Status.CONFIRMED

    def test_repeated_room_type_entries_are_merged(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=2, name='Twin')
        booking = book(hotel_a, [(twin, 1), (twin, 1)])
        assert booking.booking_rooms.count() == 2

    def test_skips_booked_and_inactive_rooms(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=3, name='Twin')
        first, second, third = twin.rooms.order_by('number')
        taken = BookingFactory(property=hotel_a, check_in_date=day(11), check_out_date=day(13))
        BookingRoomFactory(booking=taken, room=first)
        second.is_active = False
        second.save()

        booking = book(hotel_a, [(twin, 1)])

        assert booking.booking_rooms.get().room == third

    def test_back_to_back_room_can_be_reused(self, hotel_a):
        twin = make_room_type(hotel_a, name='Twin')
        earlier = BookingFactory(property=hotel_a, check_in_date=day(8), check_out_date=day(10))
        BookingRoomFactory(booking=earlier, room=twin.rooms.get())
        assert book(hotel_a, [(twin, 1)]).booking_rooms.get().room == twin.rooms.get()

    def test_price_is_a_snapshot(self, hotel_a):
        twin = make_room_type(hotel_a, name='Twin')
        booking = book(hotel_a, [(twin, 1)])

        rate = DailyRate.objects.get(room_type=twin, date=day(10))
        rate.price = Decimal('9999.00')
        rate.save()

        booking.refresh_from_db()
        assert booking.total_amount == Decimal('2000.00')
        assert BookingNightRate.objects.get(night=day(10)).price == Decimal('1000.00')

    def test_query_count_does_not_grow_with_rooms(self, hotel_a):
        small = make_room_type(hotel_a, rooms=1, name='Small')
        big = make_room_type(hotel_a, rooms=5, name='Big', start=20, prices=[Decimal('1000.00')] * 4)

        # Different emails so both bookings take the same new-guest path
        with CaptureQueriesContext(connection) as one_room:
            book(hotel_a, [(small, 1)], guest={**GUEST, 'email': 'one@example.com'})
        with CaptureQueriesContext(connection) as five_rooms:
            book(hotel_a, [(big, 5)], check_in=20, check_out=24, adults=8, guest={**GUEST, 'email': 'five@example.com'})

        assert len(five_rooms.captured_queries) == len(one_room.captured_queries)


class TestCreateBookingRejects:
    def test_not_enough_rooms(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=2, name='Twin')
        with pytest.raises(ValidationError) as exc:
            book(hotel_a, [(twin, 3)])
        assert exc.value.message_dict == {'rooms': ['ห้องประเภท Twin เหลือไม่พอ (ว่าง 2 ห้อง)']}
        assert_nothing_created()

    def test_missing_rate_for_a_night(self, hotel_a):
        twin = make_room_type(hotel_a, name='Twin', prices=[Decimal('1000.00')])  # only 1 of 2 nights
        with pytest.raises(ValidationError) as exc:
            book(hotel_a, [(twin, 1)])
        assert 'rooms' in exc.value.message_dict
        assert_nothing_created()

    def test_guests_over_capacity(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=2, name='Twin', max_occupancy=2)
        with pytest.raises(ValidationError) as exc:
            book(hotel_a, [(twin, 2)], adults=4, children=1)
        assert exc.value.message_dict == {'adults': ['จำนวนผู้เข้าพักเกินความจุของห้องที่เลือก (พักได้สูงสุด 4 คน)']}
        assert_nothing_created()

    def test_invalid_dates(self, hotel_a):
        twin = make_room_type(hotel_a, name='Twin')
        with pytest.raises(ValidationError):
            book(hotel_a, [(twin, 1)], check_in=12, check_out=10)
        assert_nothing_created()

    def test_invalid_payment_option(self, hotel_a):
        twin = make_room_type(hotel_a, name='Twin')
        with pytest.raises(ValidationError) as exc:
            book(hotel_a, [(twin, 1)], payment_option='BITCOIN')
        assert 'payment_option' in exc.value.message_dict

    def test_empty_rooms(self, hotel_a):
        with pytest.raises(ValidationError):
            book(hotel_a, [])

    def test_invalid_guest_email_rolls_back(self, hotel_a):
        twin = make_room_type(hotel_a, name='Twin')
        with pytest.raises(ValidationError):
            create_booking(
                hotel_a, check_in=day(10), check_out=day(12),
                rooms=[{'room_type_id': twin.pk, 'quantity': 1}],
                guest={**GUEST, 'email': 'not-an-email'}, adults=1, children=0, payment_option='FULL',
            )
        assert_nothing_created()

    def test_exclusion_constraint_is_last_line_of_defence(self, hotel_a, monkeypatch):
        """If the availability check is bypassed, the DB still refuses the overlap."""
        twin = make_room_type(hotel_a, name='Twin')
        taken = BookingFactory(property=hotel_a, check_in_date=day(10), check_out_date=day(12))
        BookingRoomFactory(booking=taken, room=twin.rooms.get())
        monkeypatch.setattr(services, 'booked_room_ids', lambda *args: BookingRoom.objects.none().values('room_id'))

        with pytest.raises(ValidationError) as exc:
            book(hotel_a, [(twin, 1)])

        assert exc.value.message_dict == {'rooms': [ROOM_TAKEN_MESSAGE]}
        assert Booking.objects.count() == 1  # only the pre-existing one
        assert not Guest.objects.exclude(pk=taken.guest_id).exists()


class TestCreateBookingIsolation:
    def test_room_type_of_other_property_is_404(self, hotel_a, hotel_b):
        theirs = make_room_type(hotel_b, name='Theirs')
        with pytest.raises(Http404):
            book(hotel_a, [(theirs, 1)])
        assert_nothing_created()

    def test_mixing_own_and_other_property_types_is_404(self, hotel_a, hotel_b):
        mine = make_room_type(hotel_a, name='Mine')
        theirs = make_room_type(hotel_b, name='Theirs')
        with pytest.raises(Http404):
            book(hotel_a, [(mine, 1), (theirs, 1)])
        assert_nothing_created()

    def test_other_property_bookings_do_not_block(self, hotel_a, hotel_b):
        mine = make_room_type(hotel_a, name='Twin')
        theirs = make_room_type(hotel_b, name='Twin')
        taken = BookingFactory(property=hotel_b, check_in_date=day(10), check_out_date=day(12))
        BookingRoomFactory(booking=taken, room=theirs.rooms.get())

        booking = book(hotel_a, [(mine, 1)])

        assert booking.property == hotel_a
        assert booking.booking_rooms.get().room.property == hotel_a
        assert booking.guest.property == hotel_a


def api_url(prop):
    return reverse('bookings:api-booking-create', kwargs={'property_slug': prop.slug})


def payload(rooms, check_in=10, check_out=12, **overrides):
    return {
        'check_in': day(check_in).isoformat(),
        'check_out': day(check_out).isoformat(),
        'adults': 2,
        'children': 0,
        'payment_option': 'FULL',
        'guest': GUEST,
        'rooms': [{'room_type_id': rt.pk, 'quantity': qty} for rt, qty in rooms],
        **overrides,
    }


def post(client, prop, data):
    return client.post(api_url(prop), data, content_type='application/json')


class TestBookingAPI:
    def test_creates_booking_and_returns_details(self, client, hotel_a):
        twin = make_room_type(hotel_a, rooms=2, name='Twin', prices=[Decimal('1000.00'), Decimal('1500.50')])

        response = post(client, hotel_a, payload([(twin, 2)], adults=3))

        assert response.status_code == 201
        body = response.json()
        booking = Booking.objects.get()
        assert body['id'] == str(booking.pk)
        assert body['code'] == booking.code
        assert body['status'] == 'PENDING_PAYMENT'
        assert body['nights'] == 2
        assert body['total_amount'] == '5001.00'
        assert body['guest'] == {'email': GUEST['email']}
        assert body['rooms'][0] == {
            'room_type_id': twin.pk,
            'room_type_name': 'Twin',
            'room_number': 'Twin-01',
            'subtotal': '2500.50',
            'nights': [
                {'night': day(10).isoformat(), 'price': '1000.00'},
                {'night': day(11).isoformat(), 'price': '1500.50'},
            ],
        }
        assert [room['room_number'] for room in body['rooms']] == ['Twin-01', 'Twin-02']

    def test_anonymous_guest_can_book(self, client, hotel_a):
        twin = make_room_type(hotel_a, name='Twin')
        assert post(client, hotel_a, payload([(twin, 1)])).status_code == 201

    def test_missing_fields_are_400_in_thai(self, client, hotel_a):
        response = post(client, hotel_a, {})
        assert response.status_code == 400
        body = response.json()
        assert set(body) == {'check_in', 'check_out', 'adults', 'payment_option', 'guest', 'rooms'}
        assert body['adults'] == ['กรุณาระบุจำนวนผู้ใหญ่']
        assert_nothing_created()

    @pytest.mark.parametrize('rooms, message', [
        ([], 'กรุณาเลือกห้องอย่างน้อย 1 ห้อง'),
        ([{'room_type_id': 1, 'quantity': 21}], 'จองได้สูงสุด 20 ห้องต่อครั้ง'),
        ([{'room_type_id': 1, 'quantity': 0}], 'จำนวนห้องต้องมีอย่างน้อย 1 ห้อง'),
    ])
    def test_invalid_rooms_are_400_in_thai(self, client, hotel_a, rooms, message):
        response = post(client, hotel_a, {**payload([]), 'rooms': rooms})
        assert response.status_code == 400
        assert message in str(response.json()['rooms'])

    def test_total_rooms_over_limit_is_400(self, client, hotel_a):
        rooms = [{'room_type_id': 1, 'quantity': 10}, {'room_type_id': 2, 'quantity': 11}]
        response = post(client, hotel_a, {**payload([]), 'rooms': rooms})
        assert response.json() == {'rooms': ['จองได้สูงสุด 20 ห้องต่อครั้ง']}

    def test_service_errors_are_400(self, client, hotel_a):
        twin = make_room_type(hotel_a, rooms=1, name='Twin')
        response = post(client, hotel_a, payload([(twin, 2)]))
        assert response.status_code == 400
        assert response.json() == {'rooms': ['ห้องประเภท Twin เหลือไม่พอ (ว่าง 1 ห้อง)']}

    @pytest.mark.parametrize('method', ['get', 'put', 'patch', 'delete'])
    def test_only_post_is_allowed(self, client, hotel_a, method):
        assert getattr(client, method)(api_url(hotel_a)).status_code == 405

    def test_response_query_count_does_not_grow_with_rooms(self, client, hotel_a):
        small = make_room_type(hotel_a, rooms=1, name='Small')
        big = make_room_type(hotel_a, rooms=5, name='Big', start=20, prices=[Decimal('1000.00')] * 4)

        # Different emails so both bookings take the same new-guest path
        one = payload([(small, 1)], guest={**GUEST, 'email': 'one@example.com'})
        five = payload([(big, 5)], check_in=20, check_out=24, adults=8, guest={**GUEST, 'email': 'five@example.com'})
        with CaptureQueriesContext(connection) as one_room:
            assert post(client, hotel_a, one).status_code == 201
        with CaptureQueriesContext(connection) as five_rooms:
            assert post(client, hotel_a, five).status_code == 201

        assert len(five_rooms.captured_queries) == len(one_room.captured_queries)


class TestBookingAPIIsolation:
    def test_room_type_id_of_other_property_in_body_is_404(self, client, hotel_a, hotel_b):
        theirs = make_room_type(hotel_b, name='Theirs')

        response = post(client, hotel_a, payload([(theirs, 1)]))

        assert response.status_code == 404
        assert_nothing_created()

    def test_cannot_book_into_other_property_through_its_own_url_and_my_ids(self, client, hotel_a, hotel_b):
        mine = make_room_type(hotel_a, name='Mine')
        assert post(client, hotel_b, payload([(mine, 1)])).status_code == 404
        assert_nothing_created()

    def test_unknown_or_inactive_property_is_404(self, client):
        from apps.properties.tests.factories import PropertyFactory

        inactive = PropertyFactory(slug='closed', is_active=False)
        twin = make_room_type(inactive, name='Twin')
        assert client.post('/api/p/nope/bookings/', {}, content_type='application/json').status_code == 404
        assert post(client, inactive, payload([(twin, 1)])).status_code == 404
        assert_nothing_created()


class TestGuestReuse:
    """One guest per email per property (case-insensitive); existing profiles are never overwritten."""

    def test_same_email_reuses_the_guest(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=2, name='Twin')
        first = book(hotel_a, [(twin, 1)])
        second = book(hotel_a, [(twin, 1)])
        assert first.guest_id == second.guest_id
        assert Guest.objects.count() == 1

    def test_email_match_ignores_case_and_spaces(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=2, name='Twin')
        first = book(hotel_a, [(twin, 1)])
        second = create_booking(
            hotel_a, check_in=day(10), check_out=day(12),
            rooms=[{'room_type_id': twin.pk, 'quantity': 1}],
            guest={**GUEST, 'email': '  SomChai@Example.COM '}, adults=1, children=0, payment_option='FULL',
        )
        assert second.guest_id == first.guest_id

    def test_existing_profile_is_not_overwritten(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=2, name='Twin')
        original = book(hotel_a, [(twin, 1)]).guest
        create_booking(
            hotel_a, check_in=day(10), check_out=day(12),
            rooms=[{'room_type_id': twin.pk, 'quantity': 1}],
            guest={**GUEST, 'first_name': 'Imposter', 'phone': '0999999999'},
            adults=1, children=0, payment_option='FULL',
        )
        original.refresh_from_db()
        assert (original.first_name, original.phone) == ('Somchai', '0812345678')

    def test_same_email_in_other_property_is_a_separate_guest(self, hotel_a, hotel_b):
        mine = book(hotel_a, [(make_room_type(hotel_a, name='Twin'), 1)])
        theirs = book(hotel_b, [(make_room_type(hotel_b, name='Twin'), 1)])
        assert mine.guest_id != theirs.guest_id
        assert mine.guest.property == hotel_a and theirs.guest.property == hotel_b

    def test_invalid_input_is_rejected_even_if_email_exists(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=2, name='Twin')
        book(hotel_a, [(twin, 1)])
        with pytest.raises(ValidationError) as exc:
            create_booking(
                hotel_a, check_in=day(10), check_out=day(12),
                rooms=[{'room_type_id': twin.pk, 'quantity': 1}],
                guest={**GUEST, 'phone': ''}, adults=1, children=0, payment_option='FULL',
            )
        assert 'phone' in exc.value.message_dict
        assert Booking.objects.count() == 1

    def test_api_does_not_leak_stored_name_or_phone(self, client, hotel_a):
        twin = make_room_type(hotel_a, rooms=2, name='Twin')
        book(hotel_a, [(twin, 1)])  # the real Somchai

        response = post(client, hotel_a, payload([(twin, 1)], guest={**GUEST, 'first_name': 'Stranger', 'phone': '0000000000'}))

        assert response.status_code == 201
        assert response.json()['guest'] == {'email': GUEST['email']}
        content = response.content.decode()
        assert 'Somchai' not in content and '0812345678' not in content


KEY = '3f6c1b2a-8d4e-4f7a-9b0c-1d2e3f4a5b6c'


class TestIdempotency:
    def test_same_key_returns_the_first_booking(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=3, name='Twin')
        first = create_booking(
            hotel_a, check_in=day(10), check_out=day(12), rooms=[{'room_type_id': twin.pk, 'quantity': 1}],
            guest=GUEST, adults=1, children=0, payment_option='FULL', idempotency_key=KEY,
        )
        again = create_booking(
            hotel_a, check_in=day(10), check_out=day(12), rooms=[{'room_type_id': twin.pk, 'quantity': 1}],
            guest=GUEST, adults=1, children=0, payment_option='FULL', idempotency_key=KEY,
        )
        assert again.pk == first.pk
        assert Booking.objects.count() == 1
        assert BookingRoom.objects.count() == 1

    def test_replay_works_even_when_rooms_are_now_full(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=1, name='Twin')
        kwargs = dict(
            check_in=day(10), check_out=day(12), rooms=[{'room_type_id': twin.pk, 'quantity': 1}],
            guest=GUEST, adults=1, children=0, payment_option='FULL', idempotency_key=KEY,
        )
        first = create_booking(hotel_a, **kwargs)
        assert create_booking(hotel_a, **kwargs).pk == first.pk

    def test_replay_ignores_a_different_payload(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=3, name='Twin')
        first = create_booking(
            hotel_a, check_in=day(10), check_out=day(12), rooms=[{'room_type_id': twin.pk, 'quantity': 1}],
            guest=GUEST, adults=1, children=0, payment_option='FULL', idempotency_key=KEY,
        )
        again = create_booking(
            hotel_a, check_in=day(10), check_out=day(12), rooms=[{'room_type_id': twin.pk, 'quantity': 2}],
            guest=GUEST, adults=2, children=0, payment_option='PAY_AT_HOTEL', idempotency_key=KEY,
        )
        assert again.pk == first.pk
        assert again.booking_rooms.count() == 1

    def test_different_keys_and_no_key_create_separate_bookings(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=4, name='Twin')
        for key in (KEY, '00000000-0000-4000-8000-000000000001', None, None):
            create_booking(
                hotel_a, check_in=day(10), check_out=day(12), rooms=[{'room_type_id': twin.pk, 'quantity': 1}],
                guest=GUEST, adults=1, children=0, payment_option='FULL', idempotency_key=key,
            )
        assert Booking.objects.count() == 4

    def test_key_is_scoped_to_the_property(self, hotel_a, hotel_b):
        mine = make_room_type(hotel_a, name='Twin')
        theirs = make_room_type(hotel_b, name='Twin')
        a = create_booking(
            hotel_a, check_in=day(10), check_out=day(12), rooms=[{'room_type_id': mine.pk, 'quantity': 1}],
            guest=GUEST, adults=1, children=0, payment_option='FULL', idempotency_key=KEY,
        )
        b = create_booking(
            hotel_b, check_in=day(10), check_out=day(12), rooms=[{'room_type_id': theirs.pk, 'quantity': 1}],
            guest=GUEST, adults=1, children=0, payment_option='FULL', idempotency_key=KEY,
        )
        assert a.pk != b.pk
        assert (a.property, b.property) == (hotel_a, hotel_b)

    def test_api_replay_returns_the_same_booking(self, client, hotel_a):
        twin = make_room_type(hotel_a, rooms=1, name='Twin')
        data = payload([(twin, 1)], idempotency_key=KEY)

        first = post(client, hotel_a, data)
        second = post(client, hotel_a, data)

        assert first.status_code == second.status_code == 201
        assert first.json() == second.json()
        assert Booking.objects.count() == 1

    def test_api_rejects_a_key_that_is_not_a_uuid(self, client, hotel_a):
        twin = make_room_type(hotel_a, name='Twin')
        response = post(client, hotel_a, payload([(twin, 1)], idempotency_key='click-1'))
        assert response.status_code == 400
        assert response.json() == {'idempotency_key': ['idempotency_key ต้องเป็น UUID']}


class TestGetOrCreateBooking:
    """`created` tells callers whether to do once-only work such as the confirmation email."""

    def kwargs(self, room_type, key):
        return dict(
            check_in=day(10), check_out=day(12), rooms=[{'room_type_id': room_type.pk, 'quantity': 1}],
            guest=GUEST, adults=1, children=0, payment_option='FULL', idempotency_key=key,
        )

    def test_first_call_creates_and_replay_does_not(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=2, name='Twin')
        booking, created = get_or_create_booking(hotel_a, **self.kwargs(twin, KEY))
        again, created_again = get_or_create_booking(hotel_a, **self.kwargs(twin, KEY))
        assert created is True
        assert created_again is False
        assert again.pk == booking.pk

    def test_replay_after_rooms_ran_out_is_not_created(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=1, name='Twin')
        get_or_create_booking(hotel_a, **self.kwargs(twin, KEY))
        _, created = get_or_create_booking(hotel_a, **self.kwargs(twin, KEY))
        assert created is False

    def test_without_key_every_call_is_created(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=2, name='Twin')
        assert get_or_create_booking(hotel_a, **self.kwargs(twin, None))[1] is True
        assert get_or_create_booking(hotel_a, **self.kwargs(twin, None))[1] is True
        assert Booking.objects.count() == 2

    def test_errors_still_raise(self, hotel_a):
        twin = make_room_type(hotel_a, rooms=1, name='Twin')
        get_or_create_booking(hotel_a, **self.kwargs(twin, None))
        with pytest.raises(ValidationError):
            get_or_create_booking(hotel_a, **self.kwargs(twin, KEY))

    def test_create_booking_still_returns_only_the_booking(self, hotel_a):
        twin = make_room_type(hotel_a, name='Twin')
        assert isinstance(create_booking(hotel_a, **self.kwargs(twin, KEY)), Booking)
