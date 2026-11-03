from django import forms

from apps.bookings.models import Guest

ROOM_FIELD_PREFIX = 'room_'
THAI_ID_LENGTH = 13


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
        if cleaned.get('id_type') == Guest.IdType.ID_CARD and cleaned.get('id_number'):
            # Staff often type it as 1-2345-67890-12-3; the checksum is not checked,
            # so made-up test numbers still work
            digits = cleaned['id_number'].replace('-', '').replace(' ', '')
            if not (digits.isdigit() and len(digits) == THAI_ID_LENGTH):
                self.add_error('id_number', f'เลขบัตรประชาชนต้องเป็นตัวเลข {THAI_ID_LENGTH} หลัก')
            else:
                cleaned['id_number'] = digits
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
