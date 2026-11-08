from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.views.decorators.http import require_safe

from .services import active_memberships, public_hotels


@require_safe
def hotel_list(request):
    # Public home page at /: guests pick a hotel here, staff sign in from the navbar
    return render(request, 'accounts/hotel_list.html', {'hotels': public_hotels()})


@login_required
def home(request):
    return render(request, 'accounts/home.html', {'memberships': active_memberships(request.user)})


def property_home(request):
    # Temporary landing page for /p/<property_slug>/ until the dashboard exists.
    # PropertyMiddleware has already checked login and membership.
    return render(request, 'accounts/property_home.html')
