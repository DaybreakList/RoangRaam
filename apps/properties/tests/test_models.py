from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, models

from apps.properties.models import Membership, PropertyScopedModel, property_upload_to

from .factories import MembershipFactory, PropertyFactory, UserFactory

User = get_user_model()


class ScopedThing(PropertyScopedModel):
    """Concrete model used only to exercise the abstract base class."""

    name = models.CharField(max_length=50)

    class Meta:
        app_label = 'properties'


@pytest.fixture
def scoped_thing_table(db):
    # PostgreSQL DDL is transactional: the table is dropped by the test's rollback.
    with connection.schema_editor() as editor:
        editor.create_model(ScopedThing)
    return ScopedThing


@pytest.mark.django_db
class TestUser:
    def test_create_user_with_email(self):
        user = User.objects.create_user(email='Staff@Example.COM', password='pw')
        assert user.email == 'Staff@example.com'
        assert user.check_password('pw')
        assert not user.is_staff

    def test_create_superuser(self):
        user = User.objects.create_superuser(email='admin@example.com', password='pw')
        assert user.is_staff and user.is_superuser

    def test_email_is_required(self):
        with pytest.raises(ValueError):
            User.objects.create_user(email='', password='pw')

    def test_duplicate_email_rejected(self):
        UserFactory(email='a@example.com')
        with pytest.raises(IntegrityError):
            UserFactory(email='a@example.com')


@pytest.mark.django_db
class TestProperty:
    def test_primary_key_is_uuid(self):
        prop = PropertyFactory()
        assert prop.pk.version == 4

    def test_tax_rate_is_decimal(self):
        prop = PropertyFactory()
        prop.refresh_from_db()
        assert prop.tax_rate == Decimal('7.00')
        assert isinstance(prop.tax_rate, Decimal)

    def test_duplicate_slug_rejected(self):
        PropertyFactory(slug='same')
        with pytest.raises(IntegrityError):
            PropertyFactory(slug='same')

    def test_logo_upload_path(self):
        prop = PropertyFactory()
        assert property_upload_to(prop, 'logo.png') == f'properties/{prop.pk}/logo.png'


@pytest.mark.django_db
class TestMembership:
    def test_user_can_join_multiple_properties(self):
        user = UserFactory()
        MembershipFactory(user=user, role=Membership.Role.OWNER)
        MembershipFactory(user=user, role=Membership.Role.MANAGER)
        assert user.memberships.count() == 2

    def test_duplicate_membership_in_same_property_rejected(self):
        membership = MembershipFactory()
        with pytest.raises(IntegrityError):
            MembershipFactory(
                user=membership.user,
                property=membership.property,
                role=Membership.Role.MANAGER,
            )


class TestScopedManager:
    def test_for_property_returns_only_that_property(self, scoped_thing_table):
        hotel_a = PropertyFactory()
        hotel_b = PropertyFactory()
        mine = ScopedThing.objects.create(property=hotel_a, name='a')
        ScopedThing.objects.create(property=hotel_b, name='b')

        assert list(ScopedThing.scoped.for_property(hotel_a)) == [mine]

    def test_other_property_sees_nothing(self, scoped_thing_table):
        hotel_a = PropertyFactory()
        hotel_b = PropertyFactory()
        ScopedThing.objects.create(property=hotel_a, name='a')

        assert not ScopedThing.scoped.for_property(hotel_b).exists()

    def test_upload_path_uses_owning_property(self, scoped_thing_table):
        hotel = PropertyFactory()
        thing = ScopedThing(property=hotel, name='a')
        assert property_upload_to(thing, 'x.jpg') == f'properties/{hotel.pk}/x.jpg'

    @pytest.mark.parametrize('bad_value', [None, 'some-uuid-string'])
    def test_for_property_rejects_non_property(self, scoped_thing_table, bad_value):
        with pytest.raises(TypeError):
            ScopedThing.scoped.for_property(bad_value)
