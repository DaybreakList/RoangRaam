from django import forms

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
