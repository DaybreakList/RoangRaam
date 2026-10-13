from django.contrib.auth.views import redirect_to_login
from django.http import Http404

from .models import Membership, Property

PROPERTY_URL_KWARG = 'property_slug'


def _is_public(view_func):
    if getattr(view_func, 'property_public', False):
        return True
    view_class = getattr(view_func, 'view_class', None)
    return getattr(view_class, 'property_public', False)


class PropertyMiddleware:
    """
    Resolve `request.property` from the `<slug:property_slug>` URL kwarg.

    Every URL under /p/<property_slug>/ is staff-only by default: the user must
    have a Membership in that property, otherwise the response is 404 (not 403)
    so other properties cannot even be confirmed to exist.
    Views open to guests must be marked with `@public_property_view` or
    `PublicPropertyViewMixin`.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.property = None
        request.membership = None
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        if PROPERTY_URL_KWARG not in view_kwargs:
            return None

        slug = view_kwargs.pop(PROPERTY_URL_KWARG)
        prop = Property.objects.filter(slug=slug, is_active=True).first()
        if prop is None:
            raise Http404
        request.property = prop

        if _is_public(view_func):
            return None

        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())

        membership = Membership.objects.filter(user=request.user, property=prop).first()
        if membership is None:
            raise Http404
        request.membership = membership
        return None
