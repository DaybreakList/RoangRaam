import re

from django import forms
from django.core.exceptions import ValidationError

from .models import Booking
from .services import validate_stay_dates

DATE_INPUT_FORMAT = '%Y-%m-%d'


class SearchForm(forms.Form):
    check_in = forms.DateField(
        label='วันเช็คอิน',
        widget=forms.DateInput(attrs={'type': 'date'}, format=DATE_INPUT_FORMAT),
    )
    check_out = forms.DateField(
        label='วันเช็คเอาต์',
        widget=forms.DateInput(attrs={'type': 'date'}, format=DATE_INPUT_FORMAT),
    )
    guests = forms.IntegerField(label='จำนวนผู้เข้าพัก', min_value=1, initial=2)

    def clean(self):
        cleaned_data = super().clean()
        check_in = cleaned_data.get('check_in')
        check_out = cleaned_data.get('check_out')
        # Only when both dates passed field validation; the service owns the date rules
        # and raises a ValidationError dict keyed by field name.
        if check_in and check_out:
            validate_stay_dates(check_in, check_out)
        return cleaned_data


# --- Guest booking page ---

ROOM_FIELD = re.compile(r'^room_([0-9]+)$')
QUANTITY = re.compile(r'^[0-9]{1,4}$')
# Room type ids are 32-bit integers; anything larger cannot exist, and must not reach a query
MAX_ROOM_TYPE_ID = 2**31 - 1
INVALID_ROOMS_MESSAGE = 'จำนวนห้องไม่ถูกต้อง กรุณาเลือกใหม่'
STALE_FORM_MESSAGE = 'ฟอร์มนี้หมดอายุ กรุณากดยืนยันการจองอีกครั้ง'


def read_room_quantities(data):
    """
    {room_type_id: quantity} from the `room_<id>` keys of a GET or POST.

    Other keys and quantities of 0 (or an empty box) are skipped. A quantity that
    is not a plain whole number, a negative one, or an id that cannot exist raises
    a ValidationError with a Thai message. Whether the types really belong to the
    property and have free rooms is checked later, against the property's own rows.
    """
    quantities = {}
    for key in data:
        match = ROOM_FIELD.match(key)
        if match is None:
            continue
        raw = str(data.get(key, '')).strip()
        if raw == '':
            continue
        if not QUANTITY.match(raw):
            raise ValidationError(INVALID_ROOMS_MESSAGE)
        quantity = int(raw)
        if quantity == 0:
            continue
        digits = match.group(1)
        # Checked on the length first: int() of a huge digit string is itself an error
        if len(digits) > 10 or int(digits) > MAX_ROOM_TYPE_ID:
            raise ValidationError(INVALID_ROOMS_MESSAGE)
        room_type_id = int(digits)
        quantities[room_type_id] = quantities.get(room_type_id, 0) + quantity
    return quantities


def _errors(label, **extra):
    return {
        'required': f'กรุณาระบุ{label}',
        'invalid': f'{label}ไม่ถูกต้อง',
        'max_length': f'{label}ยาวเกินไป',
        **extra,
    }


PAYMENT_OPTION_LABELS = {
    Booking.PaymentOption.FULL: 'ชำระเต็มจำนวน (ไปหน้าชำระเงินของ Stripe ทันที)',
    Booking.PaymentOption.PAY_AT_HOTEL: 'ชำระที่โรงแรม (ยืนยันการจองทันที ชำระเมื่อเข้าพัก)',
}


class BookingForm(forms.Form):
    """
    Guest details on the booking page. Chosen rooms travel as `room_<id>` keys
    next to the form (see read_room_quantities), not as form fields.
    """

    first_name = forms.CharField(label='ชื่อ', max_length=100, error_messages=_errors('ชื่อ'))
    last_name = forms.CharField(label='นามสกุล', max_length=100, error_messages=_errors('นามสกุล'))
    email = forms.EmailField(
        label='อีเมล', max_length=254,
        error_messages=_errors('อีเมล', invalid='รูปแบบอีเมลไม่ถูกต้อง'),
    )
    phone = forms.CharField(label='เบอร์โทร', max_length=30, error_messages=_errors('เบอร์โทร'))
    adults = forms.IntegerField(
        label='ผู้ใหญ่ (คน)', min_value=1,
        error_messages=_errors('จำนวนผู้ใหญ่', min_value='ต้องมีผู้ใหญ่อย่างน้อย 1 คน'),
    )
    children = forms.IntegerField(
        label='เด็ก (คน)', min_value=0,
        error_messages=_errors('จำนวนเด็ก', min_value='จำนวนเด็กต้องไม่ติดลบ'),
    )
    # No initial value: the guest has to choose
    payment_option = forms.ChoiceField(
        label='วิธีชำระเงิน',
        choices=[(value, PAYMENT_OPTION_LABELS[value]) for value in Booking.PaymentOption.values],
        widget=forms.RadioSelect,
        error_messages={'required': 'กรุณาเลือกวิธีชำระเงิน', 'invalid_choice': 'วิธีชำระเงินไม่ถูกต้อง'},
    )
    # Made when the page is first shown and sent back unchanged, so a double submit
    # returns the first booking instead of creating a second one
    idempotency_key = forms.UUIDField(
        widget=forms.HiddenInput,
        error_messages={'required': STALE_FORM_MESSAGE, 'invalid': STALE_FORM_MESSAGE},
    )
    check_in = forms.DateField(widget=forms.HiddenInput, error_messages=_errors('วันเช็คอิน'))
    check_out = forms.DateField(widget=forms.HiddenInput, error_messages=_errors('วันเช็คเอาต์'))
    # Only used to build the "search again" link
    guests = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)

    def clean(self):
        cleaned_data = super().clean()
        check_in = cleaned_data.get('check_in')
        check_out = cleaned_data.get('check_out')
        if check_in and check_out:
            validate_stay_dates(check_in, check_out)
        return cleaned_data
