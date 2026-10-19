from datetime import date, timedelta
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from django.utils import timezone
from django.utils.cache import patch_vary_headers
from django.views.generic import TemplateView

from apps.properties.permissions import Role, RoleRequiredMixin
from .services.tape_chart import WINDOW_DAYS, build_tape_chart
from .tape_chart_forms import LAST_START, TapeChartFilterForm


class TapeChartView(RoleRequiredMixin, TemplateView):
    allowed_roles = (Role.OWNER, Role.MANAGER, Role.FRONT_DESK)
    http_method_names = ['get', 'head', 'options']
    template_name = 'frontdesk/tape_chart.html'

    def get(self, request, *args, **kwargs):
        today = timezone.localdate(timezone=ZoneInfo(request.property.timezone))
        data = request.GET.copy()
        if 'start' not in data:
            data['start'] = today.isoformat()
        form = TapeChartFilterForm(data, property=request.property)
        context = self.get_context_data(form=form, chart=None)
        if form.is_valid():
            values = form.cleaned_data
            context['chart'] = build_tape_chart(request.property, **values)

            def link(day):
                params = {'start': day.isoformat()}
                if values['floor'] is not None:
                    params['floor'] = values['floor']
                if values['room_type'] is not None:
                    params['room_type'] = values['room_type'].pk
                return request.path + '?' + urlencode(params)

            start = values['start']
            step = timedelta(days=WINDOW_DAYS)
            context.update(
                previous_url=link(start - step) if start >= date.min + step else None,
                next_url=link(start + step) if start <= LAST_START - step else None,
                today_url=link(today),
            )
        partial = request.headers.get('HX-Request') == 'true' and request.headers.get('HX-History-Restore-Request') != 'true'
        template = 'frontdesk/_tape_chart_panel.html' if partial else self.template_name
        # A bound invalid search form is a successful HTML response, so HTMX displays its errors.
        response = self.response_class(request=request, template=template, context=context)
        patch_vary_headers(response, ['HX-Request', 'HX-History-Restore-Request'])
        return response
