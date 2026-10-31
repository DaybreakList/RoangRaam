from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET
from django.views.generic import TemplateView

from apps.accounts.services import active_memberships
from apps.properties.permissions import Role, RoleRequiredMixin

from .forms import DEFAULT_DAYS, DateRangeForm
from .services import daily_revenue, hotel_comparison, kpi_summary


def _range_form(request, today):
    """The range from the query string, or the last DEFAULT_DAYS days ending `today`."""
    if 'start' in request.GET or 'end' in request.GET:
        return DateRangeForm(request.GET)
    return DateRangeForm({'start': today - timedelta(days=DEFAULT_DAYS - 1), 'end': today})


class HotelDashboardView(RoleRequiredMixin, TemplateView):
    # The story is written for the hotel manager; the owner can always see it too
    allowed_roles = (Role.OWNER, Role.MANAGER)
    http_method_names = ['get', 'head', 'options']
    template_name = 'dashboard/hotel.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        hotel = self.request.property
        form = _range_form(self.request, timezone.localdate(timezone=ZoneInfo(hotel.timezone)))
        context['form'] = form
        if form.is_valid():
            start, end = form.cleaned_data['start'], form.cleaned_data['end']
            days = daily_revenue(hotel, start, end)
            context['summary'] = kpi_summary(hotel, start, end)
            context['days'] = days
            context['chart'] = [
                {'date': day.night, 'rooms_sold': day.rooms_sold, 'revenue': day.room_revenue}
                for day in days
            ]
        return context


@login_required
@require_GET
def compare(request):
    """Owner only: the hotels where the user is the owner, side by side."""
    owned = [
        membership.property for membership in active_memberships(request.user)
        if membership.role == Role.OWNER
    ]
    if not owned:
        raise PermissionDenied

    form = _range_form(request, timezone.localdate())
    context = {'form': form, 'rows': [], 'chart': []}
    if form.is_valid():
        rows = hotel_comparison(owned, form.cleaned_data['start'], form.cleaned_data['end'])
        context['rows'] = rows
        context['chart'] = [
            {
                'name': row.property.name,
                'occupancy': row.summary.occupancy,
                'adr': row.summary.adr,
                'revpar': row.summary.revpar,
            }
            for row in rows
        ]
    return render(request, 'dashboard/compare.html', context)
