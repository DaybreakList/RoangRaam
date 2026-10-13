"""URLconf used only by middleware/permission tests."""

from django.http import HttpResponse
from django.urls import path
from django.views import View
from django.views.generic import DetailView

from apps.properties.permissions import (
    PropertyScopedQuerysetMixin,
    PublicPropertyViewMixin,
    Role,
    RoleRequiredMixin,
    public_property_view,
    role_required,
)

from .scoped_model import ScopedThing


def staff_view(request):
    return HttpResponse(f'{request.property.slug}:{request.membership.role}')


@public_property_view
def public_view(request):
    return HttpResponse(request.property.slug)


@role_required(Role.OWNER, Role.MANAGER)
def manager_view(request):
    return HttpResponse('ok')


class PublicClassView(PublicPropertyViewMixin, View):
    def get(self, request):
        return HttpResponse(request.property.slug)


class ManagerClassView(RoleRequiredMixin, View):
    allowed_roles = (Role.OWNER, Role.MANAGER)

    def get(self, request):
        return HttpResponse('ok')


class NoRolesClassView(RoleRequiredMixin, View):
    def get(self, request):
        return HttpResponse('ok')


class ThingDetailView(PropertyScopedQuerysetMixin, DetailView):
    model = ScopedThing

    def render_to_response(self, context, **kwargs):
        return HttpResponse(self.object.name)


def no_property_view(request):
    return HttpResponse(str(request.property))


urlpatterns = [
    path('p/<slug:property_slug>/staff/', staff_view),
    path('p/<slug:property_slug>/public/', public_view),
    path('p/<slug:property_slug>/manager/', manager_view),
    path('p/<slug:property_slug>/public-cbv/', PublicClassView.as_view()),
    path('p/<slug:property_slug>/manager-cbv/', ManagerClassView.as_view()),
    path('p/<slug:property_slug>/no-roles-cbv/', NoRolesClassView.as_view()),
    path('p/<slug:property_slug>/things/<int:pk>/', ThingDetailView.as_view()),
    path('outside/', no_property_view),
]
