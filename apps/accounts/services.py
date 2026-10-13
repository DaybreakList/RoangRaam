from apps.properties.models import Membership


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
