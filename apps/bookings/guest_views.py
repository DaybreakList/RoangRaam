import logging

import stripe
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_safe
from django.views.decorators.vary import vary_on_headers

from apps.properties.permissions import public_property_view

from .forms import SearchForm
from .models import Booking
from .payments import checkout_is_open, start_checkout
from .services import get_available_room_types, with_room_type_details

logger = logging.getLogger(__name__)


def _search_results(property, form):
    """Room types matching a valid search form, with scoped amenities and images."""
    data = form.cleaned_data
    room_types = get_available_room_types(
        property, data['check_in'], data['check_out'], guests=data['guests'],
    )
    return list(with_room_type_details(room_types, property))


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


def _render_payment_result(request, booking_id, error=None, status=200):
    booking = get_object_or_404(Booking.scoped.for_property(request.property), pk=booking_id)
    context = {'booking': booking, 'error': error, 'checkout_open': checkout_is_open(booking)}
    return render(request, 'bookings/payment_result.html', context, status=status)


@public_property_view
@require_POST
def pay(request, booking_id):
    """Send the guest to Stripe Checkout; Stripe brings them back to payment_result."""
    result_url = request.build_absolute_uri(reverse(
        'bookings:payment-result',
        kwargs={'property_slug': request.property.slug, 'booking_id': booking_id},
    ))
    try:
        checkout_url = start_checkout(request.property, booking_id, result_url, result_url)
    except ValidationError:
        # The result page explains the booking's status (paid, expired, ...)
        return redirect(result_url)
    except stripe.StripeError:
        logger.exception('Stripe Checkout failed for booking %s', booking_id)
        error = 'ระบบชำระเงินขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง'
        return _render_payment_result(request, booking_id, error=error, status=502)
    return redirect(checkout_url)


# Stripe sends the guest back here after paying or giving up. The page only
# shows the booking's status; the webhook (not this page) confirms the booking.
@public_property_view
@require_safe
def payment_result(request, booking_id):
    return _render_payment_result(request, booking_id)
