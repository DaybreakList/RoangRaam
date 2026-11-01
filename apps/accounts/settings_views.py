import copy

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views import View

from apps.properties.permissions import Role, RoleRequiredMixin

from .forms import HotelSettingsForm

SAVED_MESSAGE = 'บันทึกการตั้งค่าโรงแรมแล้ว'


class HotelSettingsView(RoleRequiredMixin, View):
    """Hotel settings page (TASK-50): logo, check-in/out times, tax rate and policy."""

    allowed_roles = (Role.OWNER, Role.MANAGER)
    http_method_names = ['get', 'head', 'post']
    template_name = 'accounts/hotel_settings.html'

    def get(self, request, **kwargs):
        return self.render(HotelSettingsForm(instance=self.editable_copy()))

    def post(self, request, **kwargs):
        form = HotelSettingsForm(request.POST, request.FILES, instance=self.editable_copy())
        if not form.is_valid():
            return self.render(form, status=400)
        hotel = form.save(commit=False)
        # Write only the fields of this form, so it never overwrites the name or
        # limits with the values read at the start of this request
        hotel.save(update_fields=[*form.Meta.fields, 'updated_at'])
        messages.success(request, SAVED_MESSAGE)
        return redirect('hotel_settings:edit', property_slug=hotel.slug)

    def editable_copy(self):
        # The form writes its values into the instance even when they are invalid.
        # A copy keeps request.property (navbar, logo) on the saved values.
        return copy.copy(self.request.property)

    def render(self, form, status=200):
        return render(self.request, self.template_name, {'form': form}, status=status)
