from datetime import date, timedelta

from django import forms
from django.shortcuts import get_object_or_404

from apps.rooms.models import Room, RoomType
from .services.tape_chart import WINDOW_DAYS

LAST_START = date.max - timedelta(days=WINDOW_DAYS)


class TapeChartFilterForm(forms.Form):
    start = forms.DateField(label='วันเริ่มต้น', input_formats=['%Y-%m-%d'],
                           widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    floor = forms.TypedChoiceField(label='ชั้น', required=False, coerce=int, empty_value=None)
    room_type = forms.IntegerField(label='ประเภทห้อง', required=False, widget=forms.Select)

    def __init__(self, *args, property, **kwargs):
        super().__init__(*args, **kwargs)
        self.property = property
        floors = Room.scoped.for_property(property).order_by('floor').values_list('floor', flat=True).distinct()
        self.fields['floor'].choices = [('', 'ทุกชั้น')] + [(n, str(n)) for n in floors]
        types = RoomType.scoped.for_property(property).order_by('name', 'pk')
        self.fields['room_type'].widget.choices = [('', 'ทุกประเภท')] + [(t.pk, t.name) for t in types]

    def clean_start(self):
        start = self.cleaned_data['start']
        if start > LAST_START:
            raise forms.ValidationError('วันเริ่มต้นต้องมีช่วงแสดงผลได้ครบ 14 วัน')
        return start

    def clean_room_type(self):
        pk = self.cleaned_data['room_type']
        if pk is not None:
            return get_object_or_404(RoomType.scoped.for_property(self.property), pk=pk)
        return None


class TapeChartMoveForm(forms.Form):
    target_room_id = forms.IntegerField(
        label='ห้องปลายทาง', min_value=1, max_value=9223372036854775807,
        error_messages={
            'required': 'กรุณาระบุห้องปลายทาง',
            'invalid': 'รหัสห้องปลายทางต้องเป็นจำนวนเต็ม',
            'min_value': 'รหัสห้องปลายทางไม่ถูกต้อง',
            'max_value': 'รหัสห้องปลายทางไม่ถูกต้อง',
        },
    )
