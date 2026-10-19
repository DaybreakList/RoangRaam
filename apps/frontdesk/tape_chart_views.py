from django.views.generic import TemplateView

from apps.properties.permissions import Role, RoleRequiredMixin
from .services.tape_chart import build_tape_chart


class TapeChartView(RoleRequiredMixin, TemplateView):
    allowed_roles = (Role.OWNER, Role.MANAGER, Role.FRONT_DESK)
    http_method_names = ['get', 'head', 'options']
    template_name = 'frontdesk/tape_chart.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['chart'] = build_tape_chart(self.request.property)
        return context
