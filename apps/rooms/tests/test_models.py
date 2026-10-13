from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, connection, transaction
from django.db.models.deletion import ProtectedError
from django.forms import modelform_factory
from django.http import Http404
from django.shortcuts import get_object_or_404

from apps.properties.tests.factories import PropertyFactory
from apps.rooms.models import DailyRate, Room, RoomType, RoomTypeAmenity, RoomTypeImage
from .factories import (
    AmenityFactory, DailyRateFactory, RoomFactory, RoomTypeAmenityFactory,
    RoomTypeFactory, RoomTypeImageFactory,
)

pytestmark = pytest.mark.django_db
ALL_FACTORIES = [RoomTypeFactory, RoomFactory, AmenityFactory, RoomTypeAmenityFactory, RoomTypeImageFactory, DailyRateFactory]


@pytest.fixture(autouse=True)
def isolated_media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


@pytest.mark.parametrize('factory', ALL_FACTORIES)
def test_scoped_read_and_write_isolation(factory):
    mine = factory()
    other = factory()
    model = type(mine)
    queryset = model.scoped.for_property(mine.property)
    assert list(queryset) == [mine]
    with pytest.raises(Http404):
        get_object_or_404(queryset, pk=other.pk)
    assert queryset.filter(pk=other.pk).delete()[0] == 0
    assert model.objects.filter(pk=other.pk).exists()


@pytest.mark.parametrize('factory', ALL_FACTORIES)
def test_existing_record_cannot_move_properties(factory):
    obj = factory()
    original = obj.property_id
    obj.property = PropertyFactory()
    with pytest.raises(ValidationError, match='property'):
        obj.save()
    obj.refresh_from_db()
    assert obj.property_id == original


@pytest.mark.parametrize('factory', [RoomFactory, DailyRateFactory, RoomTypeImageFactory, RoomTypeAmenityFactory])
def test_reject_foreign_room_type_on_create_and_update(factory):
    obj = factory.build()
    obj.room_type = RoomTypeFactory()
    with pytest.raises(ValidationError, match='room_type'):
        obj.save()
    saved = factory()
    original_id = saved.room_type_id
    saved.room_type = RoomTypeFactory()
    with pytest.raises(ValidationError, match='room_type'):
        saved.save()
    saved.refresh_from_db()
    assert saved.room_type_id == original_id


def test_m2m_add_rejects_cross_property_amenity():
    room_type = RoomTypeFactory()
    amenity = AmenityFactory()
    with pytest.raises(ValidationError), transaction.atomic():
        room_type.amenities.add(amenity, through_defaults={'property': room_type.property})
    assert not RoomTypeAmenity.objects.exists()


def test_m2m_add_and_remove_within_property():
    room_type = RoomTypeFactory()
    amenity = AmenityFactory(property=room_type.property)
    room_type.amenities.add(amenity, through_defaults={'property': room_type.property})
    assert list(room_type.amenities.all()) == [amenity]
    room_type.amenities.remove(amenity)
    assert not room_type.amenities.exists()


@pytest.mark.parametrize('factory,field,value', [
    (RoomTypeFactory, 'name', 'Deluxe'),
    (RoomFactory, 'number', '101'),
    (AmenityFactory, 'name', 'Wi-Fi'),
])
def test_unique_per_property_but_reusable_across_properties(factory, field, value):
    first = factory(**{field: value})
    factory(**{field: value})
    with pytest.raises(ValidationError):
        factory(property=first.property, **{field: value})


def test_daily_rate_unique_per_type_and_date():
    first = DailyRateFactory()
    with pytest.raises(ValidationError):
        DailyRateFactory(property=first.property, room_type=first.room_type, date=first.date)
    DailyRateFactory(property=first.property, room_type=first.room_type, date=first.date + timedelta(days=1))
    DailyRateFactory(property=first.property, date=first.date)


def test_duplicate_amenity_link_rejected():
    link = RoomTypeAmenityFactory()
    with pytest.raises(ValidationError):
        RoomTypeAmenityFactory(property=link.property, room_type=link.room_type, amenity=link.amenity)


@pytest.mark.parametrize('price', [Decimal('-0.01'), Decimal('1.001'), Decimal('NaN'), 12.34])
def test_invalid_prices_rejected(price):
    with pytest.raises(ValidationError):
        DailyRateFactory(price=price)


@pytest.mark.parametrize('price', [Decimal('0.00'), Decimal('1234.56')])
def test_price_preserves_exact_decimal(price):
    rate = DailyRateFactory(price=price)
    rate.refresh_from_db()
    assert isinstance(rate.price, Decimal)
    assert rate.price == price


@pytest.mark.parametrize('field', ['max_occupancy', 'size_sqm'])
@pytest.mark.parametrize('value', [0, -1])
def test_room_type_requires_positive_values(field, value):
    with pytest.raises(ValidationError):
        RoomTypeFactory(**{field: value})


def test_room_status_and_number():
    room = RoomFactory(number='B-001', floor=-1)
    assert room.hk_status == Room.HousekeepingStatus.DIRTY
    assert room.is_active
    room.hk_status = Room.HousekeepingStatus.CLEAN
    room.save()
    room.hk_status = 'UNKNOWN'
    with pytest.raises(ValidationError):
        room.save()


@pytest.mark.parametrize('factory', [RoomFactory, DailyRateFactory])
def test_referenced_room_type_cannot_be_deleted(factory):
    obj = factory()
    with pytest.raises(ProtectedError):
        obj.room_type.delete()


def test_room_type_image_upload_and_form_validation():
    obj = RoomTypeImageFactory()
    assert obj.image.name.startswith(f'properties/{obj.property_id}/')
    form_class = modelform_factory(RoomTypeImage, fields=['image'])
    fake = SimpleUploadedFile('fake.png', b'not an image', content_type='image/png')
    form = form_class(data={}, files={'image': fake}, instance=obj)
    assert not form.is_valid()
    assert 'image' in form.errors


def test_bulk_writes_cannot_bypass_validation():
    room = RoomFactory()
    with pytest.raises(TypeError):
        Room.scoped.for_property(room.property).update(room_type=RoomTypeFactory())
    with pytest.raises(TypeError):
        Room.objects.bulk_update([room], ['number'])
    invalid = RoomFactory.build(property=room.property, room_type=RoomTypeFactory())
    with pytest.raises(ValidationError):
        Room.objects.bulk_create([invalid])


def test_database_rejects_duplicate_room_number_without_model_validation():
    room = RoomFactory()
    other = RoomFactory(property=room.property)
    with pytest.raises(IntegrityError), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('UPDATE rooms_room SET number = %s WHERE id = %s', [room.number, other.pk])


def test_database_rejects_negative_price_without_model_validation():
    rate = DailyRateFactory()
    with pytest.raises(IntegrityError), transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('UPDATE rooms_dailyrate SET price = %s WHERE id = %s', ['-1.00', rate.pk])


def test_explicit_existing_pk_cannot_move_property():
    saved = RoomTypeFactory()
    replacement = RoomTypeFactory.build(id=saved.pk, property=PropertyFactory())
    with pytest.raises(ValidationError, match='property'):
        replacement.save()
    saved.refresh_from_db()
    assert saved.property_id != replacement.property_id


def test_link_update_cannot_reference_foreign_amenity_id():
    link = RoomTypeAmenityFactory()
    original = link.amenity_id
    link.amenity_id = AmenityFactory().pk
    with pytest.raises(ValidationError, match='amenity'):
        link.save()
    link.refresh_from_db()
    assert link.amenity_id == original


def test_validated_rate_updates_and_bulk_creation():
    rate = DailyRateFactory()
    rate.price = Decimal('1500.25')
    rate.save(update_fields=['price'])
    rate.refresh_from_db()
    assert rate.price == Decimal('1500.25')
    next_rate = DailyRateFactory.build(
        property=rate.property, room_type=rate.room_type,
        date=rate.date + timedelta(days=1),
    )
    DailyRate.objects.bulk_create([next_rate])
    assert DailyRate.scoped.for_property(rate.property).count() == 2
