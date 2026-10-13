import pytest
from django.db import IntegrityError, connection, models

from apps.properties.models import Membership, PropertyScopedModel

from .factories import MembershipFactory, PropertyFactory, UserFactory


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
class TestProperty:
    def test_primary_key_is_uuid(self):
        prop = PropertyFactory()
        assert prop.pk.version == 4


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

    @pytest.mark.parametrize('bad_value', [None, 'some-uuid-string'])
    def test_for_property_rejects_non_property(self, scoped_thing_table, bad_value):
        with pytest.raises(TypeError):
            ScopedThing.scoped.for_property(bad_value)
