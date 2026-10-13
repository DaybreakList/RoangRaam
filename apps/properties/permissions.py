"""
Helpers for views under /p/<property_slug>/.

PropertyMiddleware already checks layer 1 (membership). These helpers add
layer 2 (role) and keep querysets scoped to `request.property`.
"""

from functools import wraps

from django.core.exceptions import PermissionDenied

from .models import Membership

Role = Membership.Role


def public_property_view(view_func):
    """Allow guests (no login, no membership) to open this view."""
    view_func.property_public = True
    return view_func


class PublicPropertyViewMixin:
    property_public = True


def _has_role(request, roles):
    membership = getattr(request, 'membership', None)
    return membership is not None and membership.role in roles


def role_required(*roles):
    """Function-view decorator: the member's role must be one of `roles`, else 403."""

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not _has_role(request, roles):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator


class RoleRequiredMixin:
    """Class-based view mixin: set `allowed_roles`, other roles get 403."""

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        # Empty allowed_roles denies everyone (fail closed)
        if not _has_role(request, self.allowed_roles):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


class PropertyScopedQuerysetMixin:
    """
    For ListView / DetailView / UpdateView etc. on a PropertyScopedModel.

    Objects of another property are simply not in the queryset, so
    `get_object()` raises 404.
    """

    model = None

    def get_queryset(self):
        return self.model.scoped.for_property(self.request.property)
