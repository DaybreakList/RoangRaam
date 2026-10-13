import factory
from django.contrib.auth import get_user_model

from apps.properties.models import Membership, Property


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()

    username = factory.Sequence(lambda n: f'user{n}')
    email = factory.LazyAttribute(lambda o: f'{o.username}@example.com')
    password = factory.django.Password('password123')


class PropertyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Property

    name = factory.Sequence(lambda n: f'Hotel {n}')


class MembershipFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Membership

    user = factory.SubFactory(UserFactory)
    property = factory.SubFactory(PropertyFactory)
    role = Membership.Role.FRONT_DESK
