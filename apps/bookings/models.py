import secrets
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateRangeField, RangeOperators
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.db.models.functions import Lower

from apps.properties.models import PropertyScopedModel

# No 0/O/1/I so codes are easy to read out over the phone
BOOKING_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
BOOKING_CODE_LENGTH = 8


def generate_booking_code():
    return ''.join(secrets.choice(BOOKING_CODE_ALPHABET) for _ in range(BOOKING_CODE_LENGTH))


def _check_same_property(instance, field_names):
    """Every related object must belong to the same property as `instance`."""
    errors = {}
    for field_name in field_names:
        related = getattr(instance, field_name, None)
        if related is not None and related.property_id != instance.property_id:
            errors[field_name] = 'ข้อมูลที่อ้างอิงต้องอยู่ในโรงแรมเดียวกัน'
    if errors:
        raise ValidationError(errors)


class Guest(PropertyScopedModel):
    class IdType(models.TextChoices):
        ID_CARD = 'ID_CARD', 'บัตรประชาชน'
        PASSPORT = 'PASSPORT', 'หนังสือเดินทาง'

    first_name = models.CharField('ชื่อ', max_length=100)
    last_name = models.CharField('นามสกุล', max_length=100)
    email = models.EmailField('อีเมล')
    phone = models.CharField('เบอร์โทร', max_length=30)
    # Filled in at check-in
    id_type = models.CharField('ประเภทเอกสาร', max_length=10, choices=IdType.choices, blank=True)
    id_number = models.CharField('เลขที่เอกสาร', max_length=50, blank=True)
    nationality = models.CharField('สัญชาติ', max_length=100, blank=True)

    class Meta:
        verbose_name = 'ผู้เข้าพัก'
        verbose_name_plural = 'ผู้เข้าพัก'
        ordering = ['first_name', 'last_name', 'pk']
        constraints = [
            # One guest profile per email per property; email compared case-insensitively
            models.UniqueConstraint(
                'property', Lower('email'),
                name='bookings_guest_property_email_uniq',
                violation_error_message='อีเมลนี้มีข้อมูลผู้เข้าพักในโรงแรมนี้แล้ว',
            ),
        ]

    def __str__(self):
        return f'{self.first_name} {self.last_name}'


class Booking(PropertyScopedModel):
    class Status(models.TextChoices):
        PENDING_PAYMENT = 'PENDING_PAYMENT', 'รอชำระเงิน'
        CONFIRMED = 'CONFIRMED', 'ยืนยันแล้ว'
        CHECKED_IN = 'CHECKED_IN', 'เช็คอินแล้ว'
        CHECKED_OUT = 'CHECKED_OUT', 'เช็คเอาต์แล้ว'
        CANCELLED = 'CANCELLED', 'ยกเลิกแล้ว'

    class PaymentOption(models.TextChoices):
        FULL = 'FULL', 'ชำระเต็มจำนวน'
        PAY_AT_HOTEL = 'PAY_AT_HOTEL', 'ชำระที่โรงแรม'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guest = models.ForeignKey(
        Guest, on_delete=models.PROTECT, related_name='bookings', verbose_name='ผู้เข้าพัก',
    )
    code = models.CharField(
        'รหัสการจอง', max_length=BOOKING_CODE_LENGTH, default=generate_booking_code, editable=False,
    )
    status = models.CharField(
        'สถานะ', max_length=20, choices=Status.choices, default=Status.PENDING_PAYMENT,
    )
    payment_option = models.CharField('วิธีชำระเงิน', max_length=20, choices=PaymentOption.choices)
    check_in_date = models.DateField('วันเช็คอิน')
    check_out_date = models.DateField('วันเช็คเอาต์')
    adults = models.PositiveSmallIntegerField('ผู้ใหญ่', default=1, validators=[MinValueValidator(1)])
    children = models.PositiveSmallIntegerField('เด็ก', default=0)
    total_amount = models.DecimalField(
        'ยอดรวม', max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField('ยกเลิกเมื่อ', null=True, blank=True)

    class Meta:
        verbose_name = 'การจอง'
        verbose_name_plural = 'การจอง'
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['property', 'code'], name='bookings_booking_property_code_uniq'),
            models.CheckConstraint(
                condition=Q(check_out_date__gt=F('check_in_date')),
                name='bookings_booking_checkout_after_checkin',
            ),
            models.CheckConstraint(condition=Q(adults__gte=1), name='bookings_booking_adults_min_1'),
            models.CheckConstraint(
                condition=Q(total_amount__gte=0), name='bookings_booking_total_nonnegative',
            ),
        ]

    def __str__(self):
        return self.code

    def clean(self):
        super().clean()
        if self.check_in_date and self.check_out_date and self.check_out_date <= self.check_in_date:
            raise ValidationError({'check_out_date': 'วันเช็คเอาต์ต้องอยู่หลังวันเช็คอิน'})
        _check_same_property(self, ['guest'])

    @property
    def nights(self):
        return (self.check_out_date - self.check_in_date).days


class BookingRoom(PropertyScopedModel):
    """
    One physical room held by a booking for `stay_range` = [check_in, check_out).

    The exclusion constraint is the last line of defence against double booking:
    two active rows for the same room can never have overlapping ranges.
    Back-to-back stays are allowed because the check-out day is excluded.
    """

    booking = models.ForeignKey(
        Booking, on_delete=models.PROTECT, related_name='booking_rooms', verbose_name='การจอง',
    )
    room_type = models.ForeignKey(
        'rooms.RoomType', on_delete=models.PROTECT, related_name='booking_rooms',
        verbose_name='ประเภทห้อง',
    )
    room = models.ForeignKey(
        'rooms.Room', on_delete=models.PROTECT, related_name='booking_rooms', verbose_name='ห้อง',
    )
    stay_range = DateRangeField('ช่วงเข้าพัก')
    # False once the booking is cancelled, so the room is released
    is_active = models.BooleanField('ใช้งานอยู่', default=True)
    subtotal = models.DecimalField(
        'ราคารวมห้องนี้', max_digits=12, decimal_places=2, default=Decimal('0.00'),
        validators=[MinValueValidator(Decimal('0.00'))],
    )
    checked_in_at = models.DateTimeField('เช็คอินเมื่อ', null=True, blank=True)
    checked_out_at = models.DateTimeField('เช็คเอาต์เมื่อ', null=True, blank=True)
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='+', verbose_name='เช็คอินโดย',
    )

    class Meta:
        verbose_name = 'ห้องในการจอง'
        verbose_name_plural = 'ห้องในการจอง'
        ordering = ['stay_range', 'pk']
        constraints = [
            ExclusionConstraint(
                name='bookings_bookingroom_no_overlap',
                expressions=[
                    ('room', RangeOperators.EQUAL),
                    ('stay_range', RangeOperators.OVERLAPS),
                ],
                condition=Q(is_active=True),
                violation_error_message='ห้องนี้ถูกจองในช่วงวันที่เลือกแล้ว',
            ),
            models.CheckConstraint(
                condition=Q(stay_range__isempty=False)
                & Q(stay_range__lower_inf=False)
                & Q(stay_range__upper_inf=False),
                name='bookings_bookingroom_stay_range_bounded',
            ),
            models.CheckConstraint(
                condition=Q(subtotal__gte=0), name='bookings_bookingroom_subtotal_nonnegative',
            ),
        ]

    def __str__(self):
        return f'{self.booking} / {self.room}'

    def clean(self):
        super().clean()
        _check_same_property(self, ['booking', 'room_type', 'room'])
        if self.room_id and self.room_type_id and self.room.room_type_id != self.room_type_id:
            raise ValidationError({'room': 'ห้องนี้ไม่ใช่ประเภทห้องที่เลือก'})


class BookingNightRate(PropertyScopedModel):
    """Price of one night, copied from DailyRate at booking time."""

    booking_room = models.ForeignKey(
        BookingRoom, on_delete=models.CASCADE, related_name='night_rates',
        verbose_name='ห้องในการจอง',
    )
    night = models.DateField('คืนวันที่')
    price = models.DecimalField(
        'ราคา', max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))],
    )

    class Meta:
        verbose_name = 'ราคารายคืน'
        verbose_name_plural = 'ราคารายคืน'
        ordering = ['night', 'pk']
        constraints = [
            models.UniqueConstraint(
                fields=['property', 'booking_room', 'night'],
                name='bookings_nightrate_property_room_night_uniq',
            ),
            models.CheckConstraint(
                condition=Q(price__gte=0), name='bookings_nightrate_price_nonnegative',
            ),
        ]

    def __str__(self):
        return f'{self.booking_room} {self.night}'

    def clean(self):
        super().clean()
        _check_same_property(self, ['booking_room'])
