import uuid

from django.conf import settings
from django.db import models


class Property(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField('ชื่อโรงแรม', max_length=200)
    address = models.TextField('ที่อยู่', blank=True)
    phone = models.CharField('เบอร์โทร', max_length=30, blank=True)
    email = models.EmailField('อีเมล', blank=True)
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
