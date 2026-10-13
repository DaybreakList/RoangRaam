from datetime import date
from decimal import Decimal

import factory

from apps.properties.tests.factories import PropertyFactory
from apps.rooms.models import Amenity, DailyRate, Room, RoomType, RoomTypeAmenity, RoomTypeImage


class RoomTypeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RoomType

    property = factory.SubFactory(PropertyFactory)
    name = factory.Sequence(lambda n: f'Type {n}')
    max_occupancy = 2
    bed_type = 'Double'
    size_sqm = 24


class RoomFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Room

    property = factory.SubFactory(PropertyFactory)
    room_type = factory.SubFactory(RoomTypeFactory, property=factory.SelfAttribute('..property'))
    number = factory.Sequence(lambda n: str(100 + n))
    floor = 1


class AmenityFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Amenity

    property = factory.SubFactory(PropertyFactory)
    name = factory.Sequence(lambda n: f'Amenity {n}')


class RoomTypeAmenityFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RoomTypeAmenity

    property = factory.SubFactory(PropertyFactory)
    room_type = factory.SubFactory(RoomTypeFactory, property=factory.SelfAttribute('..property'))
    amenity = factory.SubFactory(AmenityFactory, property=factory.SelfAttribute('..property'))


class RoomTypeImageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = RoomTypeImage

    property = factory.SubFactory(PropertyFactory)
    room_type = factory.SubFactory(RoomTypeFactory, property=factory.SelfAttribute('..property'))
    image = factory.django.ImageField(filename='room.png')


class DailyRateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = DailyRate

    property = factory.SubFactory(PropertyFactory)
    room_type = factory.SubFactory(RoomTypeFactory, property=factory.SelfAttribute('..property'))
    date = date(2026, 10, 13)
    price = Decimal('1200.00')
