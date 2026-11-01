from django import forms
from django.core.files.uploadedfile import UploadedFile

from apps.properties.models import Property

LOGO_MAX_BYTES = 2 * 1024 * 1024
LOGO_TOO_BIG_MESSAGE = 'ไฟล์โลโก้ต้องไม่เกิน 2 MB'


class HotelSettingsForm(forms.ModelForm):
    """The settings an OWNER or MANAGER edits for their own hotel (US-14)."""

    class Meta:
        model = Property
        # Only these fields: name, slug and limits stay with the admin
        fields = ['logo', 'check_in_time', 'check_out_time', 'tax_rate', 'policy_text']
        # x-init / x-on:input feed the live preview on the page (Alpine)
        widgets = {
            'logo': forms.ClearableFileInput(attrs={'accept': '.jpg,.jpeg,.png,.webp'}),
            'check_in_time': forms.TimeInput(
                attrs={'type': 'time', 'x-init': 'checkIn = $el.value', 'x-on:input': 'checkIn = $el.value'},
                format='%H:%M',
            ),
            'check_out_time': forms.TimeInput(
                attrs={'type': 'time', 'x-init': 'checkOut = $el.value', 'x-on:input': 'checkOut = $el.value'},
                format='%H:%M',
            ),
            'tax_rate': forms.NumberInput(attrs={
                'step': '0.01', 'min': '0', 'max': '100', 'inputmode': 'decimal',
                'x-init': 'tax = $el.value', 'x-on:input': 'tax = $el.value',
            }),
            'policy_text': forms.Textarea(attrs={
                'rows': 6, 'x-init': 'policy = $el.value', 'x-on:input': 'policy = $el.value',
            }),
        }
        help_texts = {
            'logo': 'JPG, PNG หรือ WEBP ไม่เกิน 2 MB',
            'tax_rate': 'ใบเสร็จที่ออกไปแล้วจะไม่เปลี่ยนตาม',
            'policy_text': 'ขึ้นบรรทัดใหม่ได้ แสดงบนหน้าค้นหาห้องและหน้ายืนยันการจอง',
        }

    def clean_logo(self):
        logo = self.cleaned_data.get('logo')
        # Only a new upload is checked; the logo already saved is left as it is
        if isinstance(logo, UploadedFile) and logo.size > LOGO_MAX_BYTES:
            raise forms.ValidationError(LOGO_TOO_BIG_MESSAGE)
        return logo
