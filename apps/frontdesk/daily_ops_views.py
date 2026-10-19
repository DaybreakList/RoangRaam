from django.views.generic import TemplateView

from apps.properties.permissions import Role, RoleRequiredMixin
from .services.daily_ops import daily_lists


class DailyOpsView(RoleRequiredMixin, TemplateView):
    # Same access as the Tape Chart, agreed by A/C for front desk pages
    allowed_roles = (Role.OWNER, Role.MANAGER, Role.FRONT_DESK)
    http_method_names = ['get', 'head', 'options']
    template_name = 'frontdesk/daily_ops/today.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['lists'] = daily_lists(self.request.property)
        return context
