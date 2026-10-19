import pytest
from django.db import connection
from django.http import Http404
from django.test.utils import CaptureQueriesContext

from apps.bookings.services import find_booking

from .factories import BookingFactory, BookingNightRateFactory, BookingRoomFactory, GuestFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def booking(hotel_a):
    guest = GuestFactory(property=hotel_a, email='somchai@example.com')
    booking = BookingFactory(property=hotel_a, guest=guest, code='K7QM2XPA')
    BookingNightRateFactory(booking_room=BookingRoomFactory(booking=booking))
    return booking


class TestFindBooking:
    def test_finds_booking_with_code_and_email(self, hotel_a, booking):
        found = find_booking(hotel_a, 'K7QM2XPA', 'somchai@example.com')
        assert found.pk == booking.pk

    def test_ignores_case_and_spaces(self, hotel_a, booking):
        assert find_booking(hotel_a, '  k7qm2xpa ', ' SomChai@Example.COM ').pk == booking.pk

    def test_returns_rooms_and_nightly_prices_without_extra_queries(self, hotel_a, booking):
        found = find_booking(hotel_a, 'K7QM2XPA', 'somchai@example.com')
        with CaptureQueriesContext(connection) as ctx:
            rooms = list(found.booking_rooms.all())
            assert rooms[0].room.number and rooms[0].room_type.name
            assert len(rooms[0].night_rates.all()) == 1
            assert found.guest.email == 'somchai@example.com'
        assert len(ctx.captured_queries) == 0

    @pytest.mark.parametrize('code, email', [
        ('ZZZZZZZZ', 'somchai@example.com'),  # wrong code
        ('K7QM2XPA', 'someone@example.com'),  # wrong email
        ('', 'somchai@example.com'),
        ('K7QM2XPA', ''),
        (None, None),
    ])
    def test_wrong_or_missing_code_or_email_is_404(self, hotel_a, booking, code, email):
        with pytest.raises(Http404):
            find_booking(hotel_a, code, email)


class TestFindBookingIsolation:
    def test_other_property_booking_is_404(self, hotel_b, booking):
        with pytest.raises(Http404):
            find_booking(hotel_b, 'K7QM2XPA', 'somchai@example.com')

    def test_same_code_in_two_properties_returns_own(self, hotel_a, hotel_b, booking):
        theirs_guest = GuestFactory(property=hotel_b, email='somchai@example.com')
        theirs = BookingFactory(property=hotel_b, guest=theirs_guest, code='K7QM2XPA')

        assert find_booking(hotel_a, 'K7QM2XPA', 'somchai@example.com').pk == booking.pk
        assert find_booking(hotel_b, 'K7QM2XPA', 'somchai@example.com').pk == theirs.pk
