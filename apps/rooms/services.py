from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.db.models.deletion import ProtectedError
from django.shortcuts import get_object_or_404

from .forms import AmenityForm, RoomForm, RoomTypeForm
from .models import Amenity, Room, RoomType


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
