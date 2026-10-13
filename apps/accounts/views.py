from django.contrib.auth.decorators import login_required
from django.shortcuts import render

from .services import active_memberships


@login_required
def home(request):
    return render(request, 'accounts/home.html', {'memberships': active_memberships(request.user)})


def property_home(request):
    # Temporary landing page for /p/<property_slug>/ until the dashboard exists.
    # PropertyMiddleware has already checked login and membership.
    return render(request, 'accounts/property_home.html')
