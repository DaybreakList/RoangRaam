from django import forms

from apps.rooms.models import Room


class RoomStatusFilterForm(forms.Form):
    floor = forms.TypedChoiceField(label='ชั้น', required=False, coerce=int, empty_value=None)
    status = forms.ChoiceField(
        label='สถานะ', required=False,
        choices=[('', 'ทุกสถานะ')] + list(Room.HousekeepingStatus.choices),
    )

    def __init__(self, *args, property, **kwargs):
        super().__init__(*args, **kwargs)
        floors = (
            Room.scoped.for_property(property).filter(is_active=True)
            .order_by('floor').values_list('floor', flat=True).distinct()
        )
        self.fields['floor'].choices = [('', 'ทุกชั้น')] + [(n, str(n)) for n in floors]

    def query(self):
        """The valid filters as query-string pairs, for links back to the same view."""
        if not self.is_valid():
            return {}
        return {key: value for key, value in self.cleaned_data.items() if value not in (None, '')}
