from datetime import date, timedelta

from django import forms
from django.shortcuts import get_object_or_404

from apps.rooms.models import Room, RoomType
from .services.tape_chart import ALLOWED_WINDOW_DAYS, WINDOW_DAYS

LAST_START = date.max - timedelta(days=WINDOW_DAYS)


class TapeChartFilterForm(forms.Form):
    start = forms.DateField(label='วันเริ่มต้น', input_formats=['%Y-%m-%d'],
                           widget=forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date'}))
    days = forms.TypedChoiceField(label='ช่วงแสดงผล', required=False, coerce=int,
                                 empty_value=WINDOW_DAYS,
                                 choices=[(n, f'{n} วัน') for n in ALLOWED_WINDOW_DAYS])
    floor = forms.TypedChoiceField(label='ชั้น', required=False, coerce=int, empty_value=None)
    room_type = forms.IntegerField(label='ประเภทห้อง', required=False, widget=forms.Select)

    def __init__(self, *args, property, **kwargs):
        super().__init__(*args, **kwargs)
        self.property = property
        floors = Room.scoped.for_property(property).order_by('floor').values_list('floor', flat=True).distinct()
        self.fields['floor'].choices = [('', 'ทุกชั้น')] + [(n, str(n)) for n in floors]
        types = RoomType.scoped.for_property(property).order_by('name', 'pk')
        self.fields['room_type'].widget.choices = [('', 'ทุกประเภท')] + [(t.pk, t.name) for t in types]

    def clean(self):
        values = super().clean()
        start, days = values.get('start'), values.get('days')
        if start and days and start > date.max - timedelta(days=days):
            self.add_error('start', f'วันเริ่มต้นต้องมีช่วงแสดงผลได้ครบ {days} วัน')
        return values

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
