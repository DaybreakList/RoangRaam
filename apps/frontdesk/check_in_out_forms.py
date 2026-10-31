from django import forms

from apps.bookings.models import Guest

ROOM_FIELD_PREFIX = 'room_'


class CheckInForm(forms.Form):
    """
    The POST body of a check-in. A room change is sent as `room_<booking_room_id>=<room_id>`;
    the service checks that both ids belong to this booking and property.
    """

    id_type = forms.ChoiceField(label='ประเภทเอกสาร', choices=Guest.IdType.choices)
    id_number = forms.CharField(label='เลขที่เอกสาร', max_length=50)
    nationality = forms.CharField(label='สัญชาติ', max_length=100, required=False)

    def clean(self):
        cleaned = super().clean()
        room_changes = {}
        for key, value in self.data.items():
            if not key.startswith(ROOM_FIELD_PREFIX) or value == '':
                continue
            booking_room_id, room_id = key[len(ROOM_FIELD_PREFIX):], value
            if not (booking_room_id.isdigit() and room_id.isdigit()):
                raise forms.ValidationError('ข้อมูลห้องที่เลือกไม่ถูกต้อง')
            room_changes[int(booking_room_id)] = int(room_id)
        cleaned['room_changes'] = room_changes
        return cleaned
