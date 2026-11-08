from apps.properties.models import Membership, Property


def public_hotels():
    """
    Active hotels for the public home page at /, sorted by name.

    Property is the scope itself, not a PropertyScopedModel, so listing every active
    hotel is allowed here (AGENTS rule 2 exception, listed in PERMISSIONS.md). Only the
    fields the page shows are loaded; all of them are already public on each hotel's
    search page.
    """
    return (
        Property.objects.filter(is_active=True)
        .only('name', 'slug', 'address', 'logo', 'check_in_time', 'check_out_time')
        .order_by('name')
    )


def active_memberships(user):
    """
    Memberships of `user` in active properties, for the property switcher.

    Membership is not a PropertyScopedModel: listing a user's own memberships
    across properties is the point here, so it is filtered by user instead.
    """
    return (
        Membership.objects.filter(user=user, property__is_active=True)
        .select_related('property')
        .order_by('property__name')
    )
