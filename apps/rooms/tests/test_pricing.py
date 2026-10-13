from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal
from threading import Barrier

import pytest
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.http import Http404
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.properties.models import Membership
from apps.properties.tests.factories import MembershipFactory, PropertyFactory, UserFactory
from apps.rooms.models import DailyRate
from apps.rooms.services import set_daily_prices
from .factories import DailyRateFactory, RoomTypeFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def fast_passwords(settings):
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


@pytest.fixture
def room_type():
    return RoomTypeFactory()


@pytest.fixture
def owner(client, room_type):
    member = MembershipFactory(property=room_type.property, role=Membership.Role.OWNER)
    client.force_login(member.user)
    return member


def url(room_type, action='price-calendar', property=None):
    kwargs = {'property_slug': (property or room_type.property).slug}
    if action != 'price-calendar':
        kwargs['pk'] = room_type.pk
    if action == 'price-day':
        kwargs['day'] = '2026-10-13'
    return reverse(f'rooms:{action}', kwargs=kwargs)


def data(**overrides):
    return {'start_date': '2026-10-13', 'end_date': '2026-10-15', 'price': '1450.25', **overrides}


def test_bulk_inclusive_upsert_preserves_other_dates_types_properties(client, room_type, owner):
    existing = DailyRateFactory(property=room_type.property, room_type=room_type,
                                date=date(2026, 10, 14), price=Decimal('99.00'))
    outside = DailyRateFactory(property=room_type.property, room_type=room_type, date=date(2026, 10, 16))
    other_type = DailyRateFactory(property=room_type.property)
    foreign = DailyRateFactory()
    response = client.post(url(room_type, 'price-bulk'), data())
    assert response.status_code == 302
    prices = list(DailyRate.scoped.for_property(room_type.property).filter(
        room_type=room_type, date__range=(date(2026, 10, 13), date(2026, 10, 15)),
    ).values_list('date', 'price'))
    assert prices == [(date(2026, 10, day), Decimal('1450.25')) for day in (13, 14, 15)]
    existing.refresh_from_db()
    assert existing.price == Decimal('1450.25')
    for untouched in (outside, other_type, foreign):
        original = untouched.price
        untouched.refresh_from_db()
        assert untouched.price == original
    assert client.post(url(room_type, 'price-bulk'), data()).status_code == 302
    assert DailyRate.objects.count() == 6


def test_day_create_edit_and_zero_are_distinct_from_missing(client, room_type, owner):
    assert client.get(url(room_type, 'price-day')).status_code == 200
    for amount in ('12.34', '0.00'):
        assert client.post(url(room_type, 'price-day'), {'price': amount}).status_code == 302
        rate = DailyRate.scoped.for_property(room_type.property).get()
        assert rate.price == Decimal(amount)
    response = client.get(url(room_type), {'room_type': room_type.pk, 'month': '2026-10'})
    cells = {cell['day'].day: cell for week in response.context['weeks'] for cell in week if cell}
    assert cells[13]['has_rate'] and cells[13]['price'] == Decimal('0.00')
    assert not cells[14]['has_rate'] and cells[14]['price'] is None
    assert client.get(url(room_type, 'price-day')).context['form'].initial['price'] == Decimal('0.00')


@pytest.mark.parametrize('changes', [
    {'end_date': '2026-10-12'}, {'start_date': 'bad'}, {'start_date': ''},
    {'end_date': '2028-01-01'}, {'price': '-0.01'}, {'price': '1.001'},
    {'price': 'NaN'}, {'price': 'Infinity'}, {'price': '10000000000.00'}, {'price': ''},
])
def test_bad_bulk_input_returns_form_error_without_writes(client, room_type, owner, changes):
    assert client.post(url(room_type, 'price-bulk'), data(**changes)).status_code == 400
    assert not DailyRate.objects.exists()


@pytest.mark.parametrize('amount', [-1, 1.1, 'NaN', '1.001'])
def test_service_validates_input(room_type, amount):
    with pytest.raises(ValidationError):
        set_daily_prices(room_type.property, room_type.pk, date(2026, 10, 13), date(2026, 10, 15), amount)
    assert not DailyRate.objects.exists()


def test_rollback_existing_and_new_rates_on_failure(client, room_type, owner, monkeypatch):
    rate = DailyRateFactory(property=room_type.property, room_type=room_type, date=date(2026, 10, 13))
    original_price = rate.price
    original_save = DailyRate.save

    def fail_last_day(self, *args, **kwargs):
        if self.date == date(2026, 10, 15):
            raise ValidationError('จำลองความผิดพลาดเพื่อทดสอบ rollback')
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(DailyRate, 'save', fail_last_day)
    response = client.post(url(room_type, 'price-bulk'), data())
    assert response.status_code == 400
    rate.refresh_from_db()
    assert rate.price == original_price
    assert DailyRate.objects.count() == 1


@pytest.mark.parametrize('action', ['price-day', 'price-bulk'])
@pytest.mark.parametrize('method', ['get', 'post', 'patch', 'delete'])
@pytest.mark.parametrize('role', [Membership.Role.OWNER, Membership.Role.FRONT_DESK, Membership.Role.HOUSEKEEPING])
def test_cross_property_routes_are_404(client, room_type, action, method, role):
    member = MembershipFactory(role=role)
    client.force_login(member.user)
    assert getattr(client, method)(url(room_type, action, property=member.property)).status_code == 404
    assert not DailyRate.objects.exists()


@pytest.mark.parametrize('role', [Membership.Role.OWNER, Membership.Role.MANAGER, Membership.Role.FRONT_DESK, Membership.Role.HOUSEKEEPING])
def test_permission_matrix(client, room_type, role):
    member = MembershipFactory(property=room_type.property, role=role)
    client.force_login(member.user)
    can_write = role in (Membership.Role.OWNER, Membership.Role.MANAGER)
    response = client.get(url(room_type))
    assert response.status_code == (403 if role == Membership.Role.HOUSEKEEPING else 200)
    if role == Membership.Role.FRONT_DESK:
        assert url(room_type, 'price-bulk').encode() not in response.content
        assert b'/day/' not in response.content
    for action in ('price-day', 'price-bulk'):
        assert client.get(url(room_type, action)).status_code == (200 if can_write else 403)
        assert client.post(url(room_type, action), data()).status_code == (302 if can_write else 403)
    assert DailyRate.objects.exists() == can_write


@pytest.mark.parametrize('field', ['property', 'property_id', 'room_type', 'room_type_id'])
@pytest.mark.parametrize('action', ['price-day', 'price-bulk'])
def test_forged_body_is_404(client, room_type, owner, field, action):
    foreign = RoomTypeFactory()
    value = foreign.property_id if field.startswith('property') else foreign.pk
    assert client.post(url(room_type, action), data(**{field: str(value)})).status_code == 404
    assert not DailyRate.objects.exists()


def test_calendar_selection_is_scoped(client, room_type, owner):
    foreign = RoomTypeFactory()
    response = client.get(url(room_type))
    assert list(response.context['filter_form'].fields['room_type'].queryset) == [room_type]
    assert client.get(url(room_type), {'room_type': foreign.pk}).status_code == 404
    with pytest.raises(Http404):
        set_daily_prices(room_type.property, foreign.pk, date(2026, 1, 1), date(2026, 1, 1), Decimal('10'))
    assert not DailyRate.objects.exists()


@pytest.mark.parametrize('month,days', [('2024-02', 29), ('2025-02', 28), ('2026-12', 31), ('0001-01', 31), ('9999-12', 31)])
def test_calendar_month_boundaries(client, room_type, owner, month, days):
    response = client.get(url(room_type), {'month': month})
    assert response.status_code == 200
    cells = [cell for week in response.context['weeks'] for cell in week if cell]
    assert len(cells) == days
    assert cells[0]['day'].day == 1 and cells[-1]['day'].day == days
    if month == '0001-01':
        assert response.context['previous_url'] is None
    if month == '9999-12':
        assert response.context['next_url'] is None


@pytest.mark.parametrize('month', ['bad', '2026-13', '', '10000-01'])
def test_bad_month_is_400(client, room_type, owner, month):
    assert client.get(url(room_type), {'month': month}).status_code == 400


def test_leap_day_and_year_crossing(room_type):
    assert set_daily_prices(room_type.property, room_type.pk, date(2024, 2, 28), date(2024, 3, 1), Decimal('1.25')) == 3
    assert set_daily_prices(room_type.property, room_type.pk, date(2026, 12, 31), date(2027, 1, 1), Decimal('2.25')) == 2
    assert DailyRate.objects.filter(date=date(2024, 2, 29)).exists()
    assert DailyRate.objects.count() == 5


def test_empty_calendar(client):
    member = MembershipFactory(role=Membership.Role.OWNER)
    client.force_login(member.user)
    path = reverse('rooms:price-calendar', kwargs={'property_slug': member.property.slug})
    response = client.get(path)
    assert response.status_code == 200
    assert 'ยังไม่มีประเภทห้อง'.encode() in response.content


def test_anonymous_membership_and_csrf(client, room_type, owner):
    client.logout()
    assert client.get(url(room_type)).status_code == 302
    client.force_login(UserFactory())
    assert client.post(url(room_type, 'price-bulk'), data()).status_code == 404
    strict = Client(enforce_csrf_checks=True)
    strict.force_login(owner.user)
    strict.get(url(room_type, 'price-bulk'))
    assert strict.post(url(room_type, 'price-bulk'), data()).status_code == 403
    assert strict.post(url(room_type, 'price-bulk'), data(csrfmiddlewaretoken=strict.cookies['csrftoken'].value)).status_code == 302


@pytest.mark.parametrize('action', ['price-day', 'price-bulk'])
@pytest.mark.parametrize('method', ['patch', 'delete'])
def test_unsupported_methods_do_not_write(client, room_type, owner, action, method):
    assert getattr(client, method)(url(room_type, action)).status_code == 405
    assert not DailyRate.objects.exists()


def test_calendar_query_count_does_not_grow_per_day(client, room_type, owner):
    with CaptureQueriesContext(connection) as first:
        assert client.get(url(room_type), {'month': '2026-10'}).status_code == 200
    set_daily_prices(room_type.property, room_type.pk, date(2026, 10, 1), date(2026, 10, 31), Decimal('10'))
    with CaptureQueriesContext(connection) as second:
        assert client.get(url(room_type), {'month': '2026-10'}).status_code == 200
    assert len(first) == len(second)


@pytest.mark.django_db(transaction=True)
def test_concurrent_writers_do_not_duplicate_or_mix_ranges():
    room_type = RoomTypeFactory()
    gate = Barrier(2)

    def write(amount):
        close_old_connections()
        try:
            prop = PropertyFactory._meta.model.objects.get(pk=room_type.property_id)
            gate.wait(timeout=10)
            return set_daily_prices(prop, room_type.pk, date(2026, 10, 13), date(2026, 10, 15), amount)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, amount) for amount in (Decimal('10.25'), Decimal('20.50'))]
        assert [future.result(timeout=20) for future in futures] == [3, 3]
    prices = list(DailyRate.objects.values_list('price', flat=True))
    assert len(prices) == 3 and len(set(prices)) == 1
