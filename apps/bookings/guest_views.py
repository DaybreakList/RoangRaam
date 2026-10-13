from django.core.exceptions import ValidationError
from django.db.models import Prefetch
from django.shortcuts import render
from django.views.decorators.http import require_safe
from django.views.decorators.vary import vary_on_headers

from apps.properties.permissions import public_property_view
from apps.rooms.models import Amenity, RoomTypeImage

from .forms import SearchForm
from .services import get_available_room_types


def _search_results(property, form):
    """Room types matching a valid search form, with scoped amenities and images."""
    guests = form.cleaned_data['guests']
    return list(
        get_available_room_types(property, form.cleaned_data['check_in'], form.cleaned_data['check_out'])
        # TODO(A): move into the service
        .filter(max_occupancy__gte=guests)
        .prefetch_related(
            Prefetch('amenities', queryset=Amenity.scoped.for_property(property)),
            Prefetch('images', queryset=RoomTypeImage.scoped.for_property(property).order_by('pk')),
        )
    )


# The same URL returns a full page or a partial, so caches must key on HX-Request.
@public_property_view
@require_safe
@vary_on_headers('HX-Request')
def search(request):
    # An empty query string means the guest has not searched yet: unbound form,
    # no results and no errors.
    form = SearchForm(request.GET or None)
    room_types = None  # None = nothing to show; [] = searched, no availability
    if form.is_valid():
        try:
            room_types = _search_results(request.property, form)
        except ValidationError as error:
            # The service re-checks dates; surface its message instead of a 500
            form.add_error(None, error)

    is_htmx = bool(request.headers.get('HX-Request'))
    template = 'bookings/_results.html' if is_htmx else 'bookings/search.html'
    return render(request, template, {'form': form, 'room_types': room_types, 'is_htmx': is_htmx})
