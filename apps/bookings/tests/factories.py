from datetime import timedelta
from decimal import Decimal

import factory
from django.utils import timezone
from psycopg.types.range import DateRange

from apps.bookings.models import Booking, BookingNightRate, BookingRoom, Guest
from apps.properties.tests.factories import PropertyFactory
from apps.rooms.tests.factories import RoomFactory


class GuestFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Guest

    property = factory.SubFactory(PropertyFactory)
    first_name = factory.Sequence(lambda n: f'Guest{n}')
    last_name = 'Test'
    email = factory.Sequence(lambda n: f'guest{n}@example.com')
    phone = '0800000000'


class BookingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Booking

    property = factory.SubFactory(PropertyFactory)
    guest = factory.SubFactory(GuestFactory, property=factory.SelfAttribute('..property'))
    status = Booking.Status.CONFIRMED
    payment_option = Booking.PaymentOption.FULL
    check_in_date = factory.LazyFunction(lambda: timezone.localdate() + timedelta(days=7))
    check_out_date = factory.LazyAttribute(lambda o: o.check_in_date + timedelta(days=2))


class BookingRoomFactory(factory.django.DjangoModelFactory):
    """Pass `booking` and `room`; property, room_type and stay_range follow from them."""

    class Meta:
        model = BookingRoom

    booking = factory.SubFactory(BookingFactory)
    property = factory.SelfAttribute('booking.property')
    room = factory.SubFactory(RoomFactory, property=factory.SelfAttribute('..property'))
    room_type = factory.SelfAttribute('room.room_type')
    stay_range = factory.LazyAttribute(
        lambda o: DateRange(o.booking.check_in_date, o.booking.check_out_date)
    )
    subtotal = Decimal('0.00')


class BookingNightRateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = BookingNightRate

    booking_room = factory.SubFactory(BookingRoomFactory)
    property = factory.SelfAttribute('booking_room.property')
    night = factory.SelfAttribute('booking_room.booking.check_in_date')
    price = Decimal('1000.00')
