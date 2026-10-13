from django import forms
from django.core.exceptions import ValidationError
from django.http import Http404

from .models import Amenity, Room, RoomType


class ScopedChoiceField(forms.ModelChoiceField):
    def to_python(self, value):
        try:
            return super().to_python(value)
        except ValidationError as error:
            if error.code == 'invalid_choice':
                raise Http404 from error
            raise


class ScopedMultipleChoiceField(forms.ModelMultipleChoiceField):
    def _check_values(self, value):
        try:
            return super()._check_values(value)
        except ValidationError as error:
            if error.code in ('invalid_choice', 'invalid_pk_value'):
                raise Http404 from error
            raise


class PropertyForm(forms.ModelForm):
    def __init__(self, *args, property, **kwargs):
        super().__init__(*args, **kwargs)
        self.instance.property = property
        if self.is_bound:
            # Property is server-owned; reject tampering rather than reassigning it.
            for name in ('property', 'property_id'):
                values = self.data.getlist(name) if hasattr(self.data, 'getlist') else [self.data.get(name)]
                if any(value is not None and str(value) != str(property.pk) for value in values):
                    raise Http404


class RoomTypeForm(PropertyForm):
    amenities = ScopedMultipleChoiceField(
        queryset=Amenity.scoped.none(), required=False,
        label='สิ่งอำนวยความสะดวก', widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = RoomType
        fields = ['name', 'max_occupancy', 'bed_type', 'size_sqm', 'description', 'amenities']
        widgets = {'description': forms.Textarea(attrs={'rows': 4})}

    def __init__(self, *args, property, **kwargs):
        super().__init__(*args, property=property, **kwargs)
        self.fields['amenities'].queryset = Amenity.scoped.for_property(property)


class RoomForm(PropertyForm):
    room_type = ScopedChoiceField(queryset=RoomType.scoped.none(), label='ประเภทห้อง')

    class Meta:
        model = Room
        fields = ['number', 'room_type', 'floor', 'hk_status', 'is_active']

    def __init__(self, *args, property, **kwargs):
        super().__init__(*args, property=property, **kwargs)
        self.fields['room_type'].queryset = RoomType.scoped.for_property(property)


class AmenityForm(PropertyForm):
    class Meta:
        model = Amenity
        fields = ['name']
