import uuid
from datetime import time
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError('ต้องระบุอีเมล')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', False)
        extra_fields.setdefault('is_superuser', False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if not extra_fields['is_staff'] or not extra_fields['is_superuser']:
            raise ValueError('superuser ต้องมี is_staff=True และ is_superuser=True')
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    """Logs in with email instead of username."""

    username = None
    email = models.EmailField('อีเมล', unique=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        verbose_name = 'ผู้ใช้'
        verbose_name_plural = 'ผู้ใช้'

    def __str__(self):
        return self.email


def property_upload_to(instance, filename):
    """Store uploads under properties/<property_id>/<filename>."""
    property_id = instance.pk if isinstance(instance, Property) else instance.property_id
    return f'properties/{property_id}/{filename}'


IMAGE_EXTENSIONS = ['jpg', 'jpeg', 'png', 'webp']


class Property(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField('ชื่อโรงแรม', max_length=200)
    slug = models.SlugField('slug', max_length=100, unique=True)
    address = models.TextField('ที่อยู่', blank=True)
    timezone = models.CharField('เขตเวลา', max_length=64, default='Asia/Bangkok')
    logo = models.ImageField(
        'โลโก้',
        upload_to=property_upload_to,
        blank=True,
        validators=[FileExtensionValidator(IMAGE_EXTENSIONS)],
    )
    check_in_time = models.TimeField('เวลาเช็คอิน', default=time(14, 0))
    check_out_time = models.TimeField('เวลาเช็คเอาต์', default=time(12, 0))
    # Percent, e.g. 7.00 means 7%
    tax_rate = models.DecimalField(
        'อัตราภาษี (%)',
        max_digits=5,
        decimal_places=2,
        default=Decimal('7.00'),
        validators=[MinValueValidator(Decimal(0)), MaxValueValidator(Decimal(100))],
    )
    policy_text = models.TextField('นโยบายโรงแรม', blank=True)
    # Not enforced yet
    max_rooms = models.PositiveIntegerField('จำนวนห้องสูงสุด', null=True, blank=True)
    max_staff = models.PositiveIntegerField('จำนวนพนักงานสูงสุด', null=True, blank=True)
    is_active = models.BooleanField('เปิดใช้งาน', default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'โรงแรม'
        verbose_name_plural = 'โรงแรม'
        ordering = ['name']

    def __str__(self):
        return self.name


class Membership(models.Model):
    class Role(models.TextChoices):
        OWNER = 'OWNER', 'เจ้าของ'
        MANAGER = 'MANAGER', 'ผู้จัดการ'
        FRONT_DESK = 'FRONT_DESK', 'พนักงานต้อนรับ'
        HOUSEKEEPING = 'HOUSEKEEPING', 'แม่บ้าน'

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name='ผู้ใช้',
    )
    property = models.ForeignKey(
        Property,
        on_delete=models.CASCADE,
        related_name='memberships',
        verbose_name='โรงแรม',
    )
    role = models.CharField('ตำแหน่ง', max_length=20, choices=Role.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'สมาชิกโรงแรม'
        verbose_name_plural = 'สมาชิกโรงแรม'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'property'],
                name='unique_membership_per_property',
            ),
        ]

    def __str__(self):
        return f'{self.user} @ {self.property} ({self.get_role_display()})'


class PropertyScopedQuerySet(models.QuerySet):
    def for_property(self, property):
        if not isinstance(property, Property):
            raise TypeError(
                f'for_property() requires a Property instance, got {type(property).__name__}'
            )
        return self.filter(property=property)


class PropertyScopedManager(models.Manager.from_queryset(PropertyScopedQuerySet)):
    pass


class PropertyScopedModel(models.Model):
    """
    Base class for every operational model.

    Views and services must query through `Model.scoped.for_property(request.property)`,
    never `Model.objects`. `objects` exists only for admin, migrations and tests.
    """

    property = models.ForeignKey(
        Property,
        on_delete=models.PROTECT,
        related_name='%(app_label)s_%(class)s_set',
        verbose_name='โรงแรม',
    )

    objects = models.Manager()
    scoped = PropertyScopedManager()

    class Meta:
        abstract = True
