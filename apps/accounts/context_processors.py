from .services import active_memberships


def property_switcher(request):
    """Expose the user's memberships to every template for the navbar switcher."""
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return {}
    # Lazy queryset: the query runs only on pages that render the switcher
    return {'switcher_memberships': active_memberships(user)}
