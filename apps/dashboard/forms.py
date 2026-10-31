from django import forms

DEFAULT_DAYS = 30
MAX_RANGE_DAYS = 366


class DateRangeForm(forms.Form):
    """The date range of a dashboard page: first and last night, both included."""

    start = forms.DateField(
        label='ตั้งแต่วันที่',
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
    )
    end = forms.DateField(
        label='ถึงวันที่',
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
    )

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get('start'), cleaned.get('end')
        if start and end:
            if end < start:
                self.add_error('end', 'วันสิ้นสุดต้องไม่ก่อนวันเริ่มต้น')
            elif (end - start).days + 1 > MAX_RANGE_DAYS:
                self.add_error('end', f'เลือกช่วงวันที่ได้ไม่เกิน {MAX_RANGE_DAYS} วัน')
        return cleaned
