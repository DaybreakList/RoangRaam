from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MinValueValidator
from django.db import models

from apps.properties.models import (
    IMAGE_EXTENSIONS,
    PropertyScopedModel,
    PropertyScopedQuerySet,
    property_upload_to,
)


class RoomDataQuerySet(PropertyScopedQuerySet):
    """Keep ORM writes on the validation path, including M2M additions."""

    def update(self, **kwargs):
        raise TypeError('Use instance.save() so room data is validated.')

    def bulk_update(self, objs, fields, **kwargs):
        raise TypeError('Use instance.save() so room data is validated.')

    def bulk_create(self, objs, **kwargs):
        if kwargs.get('update_conflicts'):
            raise TypeError('Use instance.save() for conflict updates.')
        objs = list(objs)
        for obj in objs:
            obj.full_clean()
        return super().bulk_create(objs, **kwargs)


class ValidatedRoomData(PropertyScopedModel):
    """Property is immutable after creation; parents must share it."""

    objects = RoomDataQuerySet.as_manager()
    scoped = RoomDataQuerySet.as_manager()
    property_relations = ()

    class Meta:
        abstract = True

    def clean(self):
        super().clean()
        errors = {}
        if self.pk:
            original_property = type(self).objects.filter(pk=self.pk).values_list(
                'property_id', flat=True,
            ).first()
            if original_property is not None and original_property != self.property_id:
                errors['property'] = 'ไม่สามารถย้ายข้อมูลไปยังโรงแรมอื่นได้'
        if self.property_id:
            for field_name in self.property_relations:
                field = self._meta.get_field(field_name)
                related_id = getattr(self, field.attname)
                if related_id is not None:
                    parent_property = field.related_model.objects.filter(
                        pk=related_id,
                    ).values_list('property_id', flat=True).first()
                    if parent_property is not None and parent_property != self.property_id:
                        errors[field_name] = 'ข้อมูลที่อ้างอิงต้องอยู่ในโรงแรมเดียวกัน'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class RoomType(ValidatedRoomData):
    name = models.CharField('ชื่อประเภทห้อง', max_length=100)
    max_occupancy = models.PositiveIntegerField('จำนวนผู้พักสูงสุด', validators=[MinValueValidator(1)])
    bed_type = models.CharField('ประเภทเตียง', max_length=100)
    size_sqm = models.PositiveIntegerField('ขนาดห้อง (ตร.ม.)', validators=[MinValueValidator(1)])
    description = models.TextField('รายละเอียด', blank=True)
    amenities = models.ManyToManyField('Amenity', through='RoomTypeAmenity', related_name='room_types', blank=True)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['property', 'name'], name='rooms_type_property_name_uniq'),
            models.CheckConstraint(condition=models.Q(max_occupancy__gt=0), name='rooms_type_occupancy_positive'),
            models.CheckConstraint(condition=models.Q(size_sqm__gt=0), name='rooms_type_size_positive'),
        ]

    def __str__(self):
        return self.name


class Room(ValidatedRoomData):
    class HousekeepingStatus(models.TextChoices):
        CLEAN = 'CLEAN', 'สะอาด'
        DIRTY = 'DIRTY', 'รอทำความสะอาด'

    property_relations = ('room_type',)
    room_type = models.ForeignKey(RoomType, on_delete=models.PROTECT, related_name='rooms', verbose_name='ประเภทห้อง')
    number = models.CharField('เลขห้อง', max_length=20)
    floor = models.IntegerField('ชั้น')
    hk_status = models.CharField('สถานะความสะอาด', max_length=5, choices=HousekeepingStatus.choices, default=HousekeepingStatus.DIRTY)
    is_active = models.BooleanField('เปิดใช้งาน', default=True)

    class Meta:
        ordering = ['number', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['property', 'number'], name='rooms_room_property_number_uniq'),
            models.CheckConstraint(condition=models.Q(hk_status__in=['CLEAN', 'DIRTY']), name='rooms_room_hk_status_valid'),
        ]

    def __str__(self):
        return self.number


class Amenity(ValidatedRoomData):
    name = models.CharField('สิ่งอำนวยความสะดวก', max_length=100)

    class Meta:
        ordering = ['name', 'pk']
        constraints = [models.UniqueConstraint(fields=['property', 'name'], name='rooms_amenity_property_name_uniq')]

    def __str__(self):
        return self.name


class RoomTypeAmenity(ValidatedRoomData):
    property_relations = ('room_type', 'amenity')
    room_type = models.ForeignKey(RoomType, on_delete=models.CASCADE, related_name='amenity_links')
    amenity = models.ForeignKey(Amenity, on_delete=models.CASCADE, related_name='room_type_links')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['property', 'room_type', 'amenity'], name='rooms_type_amenity_uniq')]


class RoomTypeImage(ValidatedRoomData):
    property_relations = ('room_type',)
    room_type = models.ForeignKey(RoomType, on_delete=models.CASCADE, related_name='images')
    image = models.ImageField('รูปห้อง', upload_to=property_upload_to, validators=[FileExtensionValidator(IMAGE_EXTENSIONS)])


class DailyRate(ValidatedRoomData):
    property_relations = ('room_type',)
    room_type = models.ForeignKey(RoomType, on_delete=models.PROTECT, related_name='daily_rates', verbose_name='ประเภทห้อง')
    date = models.DateField('วันที่เข้าพัก')
    price = models.DecimalField('ราคาต่อคืน', max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])

    class Meta:
        ordering = ['date', 'pk']
        constraints = [
            models.UniqueConstraint(fields=['property', 'room_type', 'date'], name='rooms_rate_property_type_date_uniq'),
            models.CheckConstraint(condition=models.Q(price__gte=0), name='rooms_rate_price_nonnegative'),
        ]

    def clean_fields(self, exclude=None):
        # Reject before DecimalField coerces a binary float into a Decimal.
        if 'price' not in (exclude or ()) and isinstance(self.price, float):
            raise ValidationError({'price': 'กรุณาระบุราคาเป็น Decimal หรือข้อความทศนิยม'})
        super().clean_fields(exclude=exclude)
