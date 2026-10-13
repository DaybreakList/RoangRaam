from calendar import monthrange
from datetime import date, timedelta

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404

from .forms import AmenityForm, PriceRangeForm, RoomForm, RoomTypeForm
from .models import Amenity, DailyRate, Room, RoomType


INVENTORY = {
    'room-types': (RoomType, RoomTypeForm, 'ประเภทห้อง'),
    'rooms': (Room, RoomForm, 'ห้องพัก'),
    'amenities': (Amenity, AmenityForm, 'สิ่งอำนวยความสะดวก'),
}


def inventory_queryset(kind, property):
    queryset = INVENTORY[kind][0].scoped.for_property(property)
    if kind == 'rooms':
        return queryset.select_related('room_type')
    if kind == 'room-types':
        return queryset.prefetch_related(Prefetch(
            'amenities', queryset=Amenity.scoped.for_property(property),
        ))
    return queryset


def save_inventory(kind, property, data, pk=None):
    model, form_class, _ = INVENTORY[kind]
    form = None
    try:
        with transaction.atomic():
            instance = None
            if pk is not None:
                instance = get_object_or_404(
                    model.scoped.for_property(property).select_for_update(), pk=pk,
                )
            form = form_class(data=data, property=property, instance=instance)
            if not form.is_valid():
                return form, None
            instance = form.save(commit=False)
            instance.save()
            if kind == 'room-types':
                instance.amenities.set(
                    form.cleaned_data['amenities'], through_defaults={'property': property},
                )
            return form, instance
    except ValidationError as error:
        if form is None:
            raise
        for field, errors in getattr(error, 'message_dict', {'__all__': error.messages}).items():
            form.add_error(field if field in form.fields else None, errors)
    except IntegrityError:
        if form is None:
            raise
        form.add_error(None, 'ข้อมูลซ้ำหรือข้อมูลที่อ้างอิงเปลี่ยนไป กรุณาตรวจสอบแล้วลองอีกครั้ง')
    return form, None


def delete_inventory(kind, property, pk):
    try:
        with transaction.atomic():
            instance = get_object_or_404(
                INVENTORY[kind][0].scoped.for_property(property).select_for_update(), pk=pk,
            )
            instance.delete()
    except ProtectedError:
        return 'ลบไม่ได้ เนื่องจากมีข้อมูลอื่นใช้งานรายการนี้อยู่'
    except IntegrityError:
        return 'ลบไม่ได้ เนื่องจากข้อมูลที่เกี่ยวข้องเปลี่ยนไป กรุณาลองอีกครั้ง'
    return None


def monthly_prices(property, room_type, month):
    start = date(month.year, month.month, 1)
    end = date(month.year, month.month, monthrange(month.year, month.month)[1])
    return dict(DailyRate.scoped.for_property(property).filter(
        room_type=room_type, date__range=(start, end),
    ).values_list('date', 'price'))


def set_daily_prices(property, room_type_id, start_date, end_date, price):
    """Serialize range writers on the parent, even when no rate rows exist yet."""
    if isinstance(price, float):
        raise ValidationError({'price': 'กรุณาระบุราคาเป็น Decimal หรือข้อความทศนิยม'})
    form = PriceRangeForm(data={
        'start_date': start_date, 'end_date': end_date, 'price': price,
    })
    if not form.is_valid():
        raise ValidationError({key: list(errors) for key, errors in form.errors.items()})
    start = form.cleaned_data['start_date']
    end = form.cleaned_data['end_date']
    amount = form.cleaned_data['price']
    days = (end - start).days + 1
    with transaction.atomic():
        room_type = get_object_or_404(
            RoomType.scoped.for_property(property).select_for_update(), pk=room_type_id,
        )
        existing = {rate.date: rate for rate in DailyRate.scoped.for_property(property)
                    .select_for_update().filter(room_type=room_type, date__range=(start, end))}
        for offset in range(days):
            day = start + timedelta(days=offset)
            rate = existing.get(day)
            if rate is None:
                rate = DailyRate(property=property, room_type=room_type, date=day)
            rate.price = amount
            rate.save()
    return days
