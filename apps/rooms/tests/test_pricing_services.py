"""Unit tests for the price calendar services moved out of the views.

The view behaviour stays covered by test_pricing.py; these tests check each
service on its own, plus the small view helpers split out in the same change.
month_grid and adjacent_months are pure, so their tests run without a database.
"""
from datetime import date
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.urls import reverse

from apps.properties.models import Membership
from apps.properties.tests.factories import MembershipFactory, PropertyFactory
from apps.rooms import services
from apps.rooms.forms import DailyPriceForm, PriceRangeForm
from apps.rooms.models import DailyRate
from apps.rooms.services import adjacent_months, daily_price, month_grid, save_prices
from .factories import DailyRateFactory, RoomTypeFactory


def days_of(weeks):
    return [cell['day'] for week in weeks for cell in week if cell]


@pytest.mark.parametrize('month,days', [
    (date(2026, 10, 1), 31), (date(2024, 2, 1), 29), (date(2025, 2, 1), 28),
    (date(1, 1, 1), 31), (date(9999, 12, 1), 31),
])
def test_month_grid_has_every_day_in_full_weeks(month, days):
    weeks = month_grid(month, {})
    assert all(len(week) == 7 for week in weeks)
    assert days_of(weeks) == [date(month.year, month.month, day) for day in range(1, days + 1)]


def test_month_grid_starts_on_monday_and_pads_outside_days():
    weeks = month_grid(date(2026, 10, 1), {})
    lead = date(2026, 10, 1).weekday()  # Monday is 0
    assert weeks[0][:lead] == [None] * lead and weeks[0][lead]['day'] == date(2026, 10, 1)
    tail = 6 - date(2026, 10, 31).weekday()
    assert weeks[-1][7 - tail:] == [None] * tail
    # February 2021 starts on a Monday and has 28 days: four full weeks, no padding
    assert [cell is None for week in month_grid(date(2021, 2, 1), {}) for cell in week] == [False] * 28


def test_month_grid_keeps_zero_price_apart_from_missing():
    prices = {date(2026, 10, 13): Decimal('0.00'), date(2026, 10, 14): Decimal('1450.25')}
    cells = {cell['day'].day: cell for week in month_grid(date(2026, 10, 1), prices) for cell in week if cell}
    assert cells[13] == {'day': date(2026, 10, 13), 'has_rate': True, 'price': Decimal('0.00')}
    assert cells[14] == {'day': date(2026, 10, 14), 'has_rate': True, 'price': Decimal('1450.25')}
    assert cells[15] == {'day': date(2026, 10, 15), 'has_rate': False, 'price': None}


@pytest.mark.parametrize('month,previous,following', [
    (date(2026, 1, 1), date(2025, 12, 1), date(2026, 2, 1)),
    (date(2026, 12, 1), date(2026, 11, 1), date(2027, 1, 1)),
    (date(2024, 2, 1), date(2024, 1, 1), date(2024, 3, 1)),
    (date(2024, 3, 15), date(2024, 2, 1), date(2024, 4, 1)),
    (date(1, 1, 1), None, date(1, 2, 1)),
    (date(9999, 12, 1), date(9999, 11, 1), None),
])
def test_adjacent_months(month, previous, following):
    assert adjacent_months(month) == (previous, following)


def test_adjacent_months_matches_the_old_index_formula():
    # The view used this arithmetic before the refactor; check every month near both ends
    def old(month):
        index = month.year * 12 + month.month - 1
        previous = date((index - 1) // 12, (index - 1) % 12 + 1, 1) if index > 12 else None
        following = date((index + 1) // 12, (index + 1) % 12 + 1, 1) if index < 119999 else None
        return previous, following

    for year in (1, 2, 2024, 2025, 9998, 9999):
        for number in range(1, 13):
            assert adjacent_months(date(year, number, 1)) == old(date(year, number, 1))


@pytest.mark.django_db
def test_daily_price_reads_one_day_of_one_hotel():
    rate = DailyRateFactory(date=date(2026, 10, 13), price=Decimal('0.00'))
    DailyRateFactory(property=rate.property, date=date(2026, 10, 14), price=Decimal('9.00'))
    assert daily_price(rate.property, rate.room_type, date(2026, 10, 13)) == Decimal('0.00')
    # The 14th belongs to another room type of the same hotel
    assert daily_price(rate.property, rate.room_type, date(2026, 10, 14)) is None
    assert daily_price(rate.property, rate.room_type, date(2026, 10, 15)) is None
    # Another hotel never reads this hotel's rate
    assert daily_price(PropertyFactory(), rate.room_type, date(2026, 10, 13)) is None


@pytest.mark.django_db
def test_save_prices_writes_a_range_or_one_day():
    room_type = RoomTypeFactory()
    form = PriceRangeForm({'start_date': '2026-10-13', 'end_date': '2026-10-15', 'price': '10.50'})
    assert save_prices(room_type.property, room_type.pk, form) == (date(2026, 10, 13), 3)
    form = DailyPriceForm({'price': '0'})
    assert save_prices(room_type.property, room_type.pk, form, date(2026, 10, 20)) == (date(2026, 10, 20), 1)
    assert list(DailyRate.objects.order_by('date').values_list('date', 'price')) == [
        (date(2026, 10, 13), Decimal('10.50')), (date(2026, 10, 14), Decimal('10.50')),
        (date(2026, 10, 15), Decimal('10.50')), (date(2026, 10, 20), Decimal('0.00')),
    ]


@pytest.mark.django_db
def test_save_prices_returns_none_and_keeps_errors_on_the_form():
    room_type = RoomTypeFactory()
    form = PriceRangeForm({'start_date': '2026-10-15', 'end_date': '2026-10-13', 'price': '1'})
    assert save_prices(room_type.property, room_type.pk, form) is None
    assert 'end_date' in form.errors
    assert not DailyRate.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize('error,field,message', [
    (ValidationError({'price': 'ราคาไม่ถูกต้อง'}), 'price', 'ราคาไม่ถูกต้อง'),
    (ValidationError('ข้อผิดพลาดทั่วไป'), '__all__', 'ข้อผิดพลาดทั่วไป'),
    (IntegrityError('race'), '__all__', 'ข้อมูลราคาเปลี่ยนไประหว่างบันทึก กรุณาลองอีกครั้ง'),
])
def test_save_prices_maps_service_errors_to_the_form(monkeypatch, error, field, message):
    room_type = RoomTypeFactory()

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(services, 'set_daily_prices', fail)
    form = DailyPriceForm({'price': '1'})
    assert save_prices(room_type.property, room_type.pk, form, date(2026, 10, 13)) is None
    assert form.errors[field] == [message]


@pytest.mark.django_db
def test_save_prices_rejects_another_hotels_room_type():
    foreign = RoomTypeFactory()
    form = DailyPriceForm({'price': '1'})
    with pytest.raises(Http404):
        save_prices(PropertyFactory(), foreign.pk, form, date(2026, 10, 13))
    assert not DailyRate.objects.exists()


@pytest.mark.django_db
def test_view_helpers_turn_bad_input_into_404_or_405(client):
    # selected_room_type and parse_day were split out of the views in the same refactor
    member = MembershipFactory(role=Membership.Role.OWNER)
    room_type = RoomTypeFactory(property=member.property)
    client.force_login(member.user)
    calendar = reverse('rooms:price-calendar', kwargs={'property_slug': member.property.slug})
    for raw in ('abc', '1.5', '99999999999999999999999'):
        assert client.get(calendar, {'room_type': raw}).status_code == 404
    for day in ('2026-13-01', '2026-02-30', 'bad'):
        path = reverse('rooms:price-day', kwargs={
            'property_slug': member.property.slug, 'pk': room_type.pk, 'day': day,
        })
        assert client.get(path).status_code == 404
        assert client.post(path, {'price': '1'}).status_code == 404
    assert client.post(calendar).status_code == 405
    assert not DailyRate.objects.exists()
