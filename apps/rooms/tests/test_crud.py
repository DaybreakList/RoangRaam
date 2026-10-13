import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.properties.models import Membership
from apps.properties.tests.factories import MembershipFactory, PropertyFactory, UserFactory
from apps.rooms.models import Amenity, Room, RoomType
from .factories import AmenityFactory, DailyRateFactory, RoomFactory, RoomTypeFactory

pytestmark = pytest.mark.django_db
CASES = [('room-types', RoomTypeFactory), ('rooms', RoomFactory), ('amenities', AmenityFactory)]


@pytest.fixture(autouse=True)
def fast_passwords(settings):
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


@pytest.fixture
def hotel():
    return PropertyFactory()


@pytest.fixture
def owner(client, hotel):
    membership = MembershipFactory(property=hotel, role=Membership.Role.OWNER)
    client.force_login(membership.user)
    return membership


def url(hotel, kind, action='list', obj=None):
    kwargs = {'property_slug': hotel.slug}
    if obj is not None:
        kwargs['pk'] = obj.pk
    return reverse(f'rooms:{kind}-{action}', kwargs=kwargs)


def payload(kind, hotel, name='New item'):
    if kind == 'room-types':
        return {'name': name, 'max_occupancy': 2, 'bed_type': 'Double', 'size_sqm': 24, 'description': 'รายละเอียด'}
    if kind == 'rooms':
        return {'number': name, 'room_type': RoomTypeFactory(property=hotel).pk,
                'floor': 1, 'hk_status': 'CLEAN', 'is_active': 'on'}
    return {'name': name}


@pytest.mark.parametrize('kind,factory', CASES)
@pytest.mark.parametrize('role', [Membership.Role.OWNER, Membership.Role.MANAGER])
def test_full_crud(client, hotel, kind, factory, role):
    member = MembershipFactory(property=hotel, role=role)
    client.force_login(member.user)
    assert client.get(url(hotel, kind, 'create')).status_code == 200
    data = payload(kind, hotel)
    response = client.post(url(hotel, kind, 'create'), data)
    assert response.status_code == 302
    obj = factory._meta.model.scoped.for_property(hotel).get()
    assert client.get(response.url).status_code == 200
    assert str(obj).encode() in client.get(url(hotel, kind)).content
    assert client.get(url(hotel, kind, 'edit', obj)).status_code == 200
    key = 'number' if kind == 'rooms' else 'name'
    data[key] = 'Changed item'
    assert client.post(url(hotel, kind, 'edit', obj), data).status_code == 302
    obj.refresh_from_db()
    assert str(obj) == 'Changed item'
    assert client.get(url(hotel, kind, 'delete', obj)).status_code == 200
    assert factory._meta.model.objects.filter(pk=obj.pk).exists()
    assert client.post(url(hotel, kind, 'delete', obj)).status_code == 302
    assert not factory._meta.model.objects.filter(pk=obj.pk).exists()


@pytest.mark.parametrize('kind,factory', CASES)
@pytest.mark.parametrize('action', ['detail', 'edit', 'delete'])
@pytest.mark.parametrize('method', ['get', 'post', 'patch', 'delete'])
@pytest.mark.parametrize('role', [Membership.Role.OWNER, Membership.Role.FRONT_DESK, Membership.Role.HOUSEKEEPING])
def test_foreign_object_is_404_for_every_method(client, hotel, kind, factory, action, method, role):
    member = MembershipFactory(property=hotel, role=role)
    client.force_login(member.user)
    foreign = factory()
    before = factory._meta.model.objects.filter(pk=foreign.pk).values().get()
    response = getattr(client, method)(url(hotel, kind, action, foreign))
    assert response.status_code == 404
    assert factory._meta.model.objects.filter(pk=foreign.pk).values().get() == before


@pytest.mark.parametrize('kind,factory', CASES)
@pytest.mark.parametrize('role', [Membership.Role.FRONT_DESK, Membership.Role.HOUSEKEEPING])
def test_read_and_write_roles(client, hotel, kind, factory, role):
    member = MembershipFactory(property=hotel, role=role)
    client.force_login(member.user)
    obj = factory(property=hotel)
    read_status = 200 if role == Membership.Role.FRONT_DESK else 403
    assert client.get(url(hotel, kind)).status_code == read_status
    detail = client.get(url(hotel, kind, 'detail', obj))
    assert detail.status_code == read_status
    if read_status == 200:
        assert url(hotel, kind, 'edit', obj).encode() not in detail.content
    for action in ('create', 'edit', 'delete'):
        target = url(hotel, kind, action, None if action == 'create' else obj)
        assert client.get(target).status_code == 403
        assert client.post(target, payload(kind, hotel)).status_code == 403
    assert factory._meta.model.objects.filter(pk=obj.pk).exists()


@pytest.mark.parametrize('kind,factory', CASES)
def test_membership_required_and_anonymous_redirect(client, hotel, kind, factory):
    obj = factory(property=hotel)
    assert client.get(url(hotel, kind)).status_code == 302
    client.force_login(UserFactory())
    for method in ('get', 'post', 'patch', 'delete'):
        assert getattr(client, method)(url(hotel, kind, 'detail', obj)).status_code == 404


@pytest.mark.parametrize('kind,factory', CASES)
def test_list_and_form_choices_are_scoped(client, hotel, owner, kind, factory):
    mine = factory(property=hotel)
    foreign = factory()
    response = client.get(url(hotel, kind))
    assert str(mine).encode() in response.content
    assert str(foreign).encode() not in response.content
    response = client.get(url(hotel, kind, 'create'))
    if kind == 'rooms':
        choices = response.context['form'].fields['room_type'].queryset
        assert mine.room_type in choices and foreign.room_type not in choices
    elif kind == 'room-types':
        local_amenity = AmenityFactory(property=hotel)
        foreign_amenity = AmenityFactory()
        response = client.get(url(hotel, kind, 'create'))
        choices = response.context['form'].fields['amenities'].queryset
        assert local_amenity in choices and foreign_amenity not in choices


@pytest.mark.parametrize('action', ['create', 'edit'])
@pytest.mark.parametrize('kind,factory', CASES)
def test_property_payload_cannot_reassign_hotel(client, hotel, owner, kind, factory, action):
    obj = factory(property=hotel) if action == 'edit' else None
    data = payload(kind, hotel)
    data['property'] = str(PropertyFactory().pk)
    before = factory._meta.model.objects.count()
    assert client.post(url(hotel, kind, action, obj), data).status_code == 404
    assert factory._meta.model.objects.count() == before
    if obj:
        obj.refresh_from_db()
        assert obj.property_id == hotel.pk


@pytest.mark.parametrize('action', ['create', 'edit'])
def test_foreign_room_type_in_body_returns_404(client, hotel, owner, action):
    obj = RoomFactory(property=hotel) if action == 'edit' else None
    data = payload('rooms', hotel)
    data['room_type'] = RoomTypeFactory().pk
    before = Room.objects.count()
    assert client.post(url(hotel, 'rooms', action, obj), data).status_code == 404
    assert Room.objects.count() == before
    if obj:
        original_id = obj.room_type_id
        obj.refresh_from_db()
        assert obj.room_type_id == original_id


@pytest.mark.parametrize('action', ['create', 'edit'])
def test_foreign_amenity_is_404_and_does_not_partially_save(client, hotel, owner, action):
    obj = RoomTypeFactory(property=hotel) if action == 'edit' else None
    local = AmenityFactory(property=hotel)
    foreign = AmenityFactory()
    data = payload('room-types', hotel)
    data['amenities'] = [local.pk, foreign.pk]
    before = RoomType.objects.count()
    assert client.post(url(hotel, 'room-types', action, obj), data).status_code == 404
    assert RoomType.objects.count() == before
    if obj:
        original_name = obj.name
        obj.refresh_from_db()
        assert obj.name == original_name and not obj.amenities.exists()


def test_room_type_amenities_saved_and_removed(client, hotel, owner):
    data = payload('room-types', hotel)
    amenity = AmenityFactory(property=hotel)
    data['amenities'] = [amenity.pk]
    assert client.post(url(hotel, 'room-types', 'create'), data).status_code == 302
    obj = RoomType.scoped.for_property(hotel).get()
    assert list(obj.amenities.all()) == [amenity]
    data['amenities'] = []
    assert client.post(url(hotel, 'room-types', 'edit', obj), data).status_code == 302
    assert not obj.amenities.exists()


@pytest.mark.parametrize('kind,factory', CASES)
def test_duplicate_is_form_error(client, hotel, owner, kind, factory):
    existing = factory(property=hotel)
    data = payload(kind, hotel, str(existing))
    response = client.post(url(hotel, kind, 'create'), data)
    assert response.status_code == 400
    assert response.context['form'].errors


@pytest.mark.parametrize('field,value', [('max_occupancy', 0), ('size_sqm', -1), ('name', '')])
def test_invalid_fields_are_rejected_server_side(client, hotel, owner, field, value):
    data = payload('room-types', hotel)
    data[field] = value
    response = client.post(url(hotel, 'room-types', 'create'), data)
    assert response.status_code == 400
    assert field in response.context['form'].errors
    assert not RoomType.scoped.for_property(hotel).exists()


@pytest.mark.parametrize('factory', [RoomFactory, DailyRateFactory])
def test_protected_room_type_delete_returns_conflict(client, hotel, owner, factory):
    child = factory(property=hotel)
    response = client.post(url(hotel, 'room-types', 'delete', child.room_type))
    assert response.status_code == 409
    assert RoomType.objects.filter(pk=child.room_type_id).exists()
    assert factory._meta.model.objects.filter(pk=child.pk).exists()


@pytest.mark.parametrize('kind,factory', CASES)
@pytest.mark.parametrize('method', ['patch', 'delete'])
def test_unsupported_methods_are_405_for_local_objects(client, hotel, owner, kind, factory, method):
    obj = factory(property=hotel)
    for action in ('detail', 'edit', 'delete'):
        assert getattr(client, method)(url(hotel, kind, action, obj)).status_code == 405
    assert factory._meta.model.objects.filter(pk=obj.pk).exists()


def test_csrf_required_for_mutation(hotel, owner):
    client = Client(enforce_csrf_checks=True)
    client.force_login(owner.user)
    target = url(hotel, 'amenities', 'create')
    client.get(target)
    assert client.post(target, {'name': 'Wi-Fi'}).status_code == 403
    token = client.cookies['csrftoken'].value
    assert client.post(target, {'name': 'Wi-Fi', 'csrfmiddlewaretoken': token}).status_code == 302


@pytest.mark.parametrize('kind,factory', CASES)
def test_list_query_count_does_not_grow_per_row(client, hotel, owner, kind, factory):
    factory(property=hotel)
    with CaptureQueriesContext(connection) as first:
        assert client.get(url(hotel, kind)).status_code == 200
    factory.create_batch(6, property=hotel)
    with CaptureQueriesContext(connection) as second:
        assert client.get(url(hotel, kind)).status_code == 200
    assert len(second) == len(first)


def test_pagination_and_escaping(client, hotel, owner):
    AmenityFactory.create_batch(26, property=hotel)
    bad = AmenityFactory(property=hotel, name='<script>alert(1)</script>')
    response = client.get(url(hotel, 'amenities'))
    assert len(response.context['rows']) == 25
    assert response.context['page'].has_next()
    response = client.get(url(hotel, 'amenities', 'detail', bad))
    assert b'&lt;script&gt;' in response.content
    assert b'<script>alert(1)</script>' not in response.content
