from urllib.parse import urlencode

from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from apps.properties.permissions import Role, RoleRequiredMixin, role_required

from .room_status_forms import RoomStatusFilterForm
from .services.room_status import mark_room_clean, room_status_board

# Agreed for US-12: everyone at the hotel sees the board, the front desk only reads it
VIEW_ROLES = (Role.OWNER, Role.MANAGER, Role.FRONT_DESK, Role.HOUSEKEEPING)
CLEAN_ROLES = (Role.OWNER, Role.MANAGER, Role.HOUSEKEEPING)


class RoomStatusView(RoleRequiredMixin, TemplateView):
    allowed_roles = VIEW_ROLES
    http_method_names = ['get', 'head', 'options']
    template_name = 'frontdesk/room_status/index.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        hotel = self.request.property
        form = RoomStatusFilterForm(self.request.GET or None, property=hotel)
        filters = form.cleaned_data if form.is_bound and form.is_valid() else {}
        context.update(
            form=form,
            filters=form.query() if form.is_bound else {},
            board=room_status_board(hotel, floor=filters.get('floor'), status=filters.get('status')),
            can_mark_clean=self.request.membership.role in CLEAN_ROLES,
        )
        return context


@require_POST
@role_required(*CLEAN_ROLES)
def mark_clean_view(request, room_id):
    room, changed = mark_room_clean(request.property, room_id)
    if changed:
        messages.success(request, f'ห้อง {room.number} พร้อมให้เข้าพักแล้ว')
    else:
        messages.info(request, f'ห้อง {room.number} สะอาดอยู่แล้ว')

    # Keep the filters the housekeeper was using; only values that validate go back
    form = RoomStatusFilterForm(request.POST, property=request.property)
    url = reverse('room_status:index', kwargs={'property_slug': request.property.slug})
    query = form.query()
    return redirect(f'{url}?{urlencode(query)}' if query else url)
