"""
Guest self-service: find a booking with its code and email, then cancel it.

Everything is POST, so the code and the email never appear in a URL, and nothing is
kept in the session: each step carries them in hidden fields and looks the booking
up again. The cancel itself (refund, status, rooms) is cancellation.cancel_booking().
"""

from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import render
from django.views.decorators.http import require_http_methods, require_POST

from apps.properties.permissions import public_property_view

from .cancellation import CANCELLABLE_STATUSES, cancel_booking
from .forms import BookingLookupForm
from .models import Booking
from .payments import checkout_is_open
from .services import booking_with_details, find_booking

# One message for a wrong code, a wrong email and another hotel's booking
NOT_FOUND_MESSAGE = 'ไม่พบการจอง กรุณาตรวจสอบรหัสการจองและอีเมล'
NOT_CANCELLABLE_MESSAGE = 'การจองนี้ไม่สามารถยกเลิกได้ในสถานะปัจจุบัน'
CANCELLED_MESSAGE = 'ยกเลิกการจองเรียบร้อยแล้ว'
REFUNDED_MESSAGE = 'คืนเงินเต็มจำนวนแล้ว'


def _room_groups(booking):
    """
    [{'name', 'count', 'nights': [night rates]}] per room type, in the order the rooms are listed.

    Never the room numbers. The nightly prices come from the first room of each type: rooms
    of one type are priced from the same daily rates when the booking is made.
    """
    groups = {}
    for booking_room in booking.booking_rooms.all():
        group = groups.get(booking_room.room_type_id)
        if group is None:
            groups[booking_room.room_type_id] = {
                'name': booking_room.room_type.name,
                'count': 1,
                'nights': list(booking_room.night_rates.all()),
            }
        else:
            group['count'] += 1
    return list(groups.values())


def _render_lookup(request, form, not_found=False):
    context = {'form': form, 'not_found': not_found, 'not_found_message': NOT_FOUND_MESSAGE}
    return render(request, 'bookings/manage_booking.html', context)


def _render_detail(request, booking, notice=None, error=None):
    full = booking.payment_option == Booking.PaymentOption.FULL
    pending = booking.status == Booking.Status.PENDING_PAYMENT
    context = {
        'booking': booking,
        'room_groups': _room_groups(booking),
        # The Stripe page can be opened only until CHECKOUT_DEADLINE; after that the button would only lead to "expired"
        'can_pay': full and checkout_is_open(booking),
        'payment_expired': full and pending and not checkout_is_open(booking),
        'can_cancel': booking.status in CANCELLABLE_STATUSES,
        'notice': notice,
        'error': error,
    }
    return render(request, 'bookings/manage_booking.html', context)


def _render_cancel_confirm(request, booking):
    context = {'booking': booking, 'room_groups': _room_groups(booking)}
    return render(request, 'bookings/manage_booking_cancel.html', context)


def _lookup(request):
    """
    (booking, None) for a code and email that match a booking of this property,
    else (None, response): the form with its errors, or with the not-found message.
    """
    form = BookingLookupForm(request.POST)
    if not form.is_valid():
        return None, _render_lookup(request, form)
    try:
        booking = find_booking(request.property, form.cleaned_data['code'], form.cleaned_data['email'])
    except Http404:
        return None, _render_lookup(request, form, not_found=True)
    return booking, None


# GET shows an empty form (a query string is ignored on purpose); POST shows the booking
@public_property_view
@require_http_methods(['GET', 'POST'])
def manage_booking(request):
    if request.method == 'GET':
        return _render_lookup(request, BookingLookupForm())
    booking, response = _lookup(request)
    if response is not None:
        return response
    return _render_detail(request, booking)


def _cancel(request, booking):
    # Read before the cancel: only a paid FULL booking has money to give back
    refunded = (
        booking.payment_option == Booking.PaymentOption.FULL
        and booking.status == Booking.Status.CONFIRMED
    )
    try:
        cancel_booking(request.property, booking.pk)
    except ValidationError as error:
        # Not cancellable any more, or the refund failed: the booking is left as it was
        fresh = booking_with_details(request.property, booking.pk)
        return _render_detail(request, fresh, error=' '.join(error.message_dict['status']))
    fresh = booking_with_details(request.property, booking.pk)
    notice = f'{CANCELLED_MESSAGE} {REFUNDED_MESSAGE}' if refunded else CANCELLED_MESSAGE
    return _render_detail(request, fresh, notice=notice)


# Step 1 (no `confirm` field) asks the guest to confirm and changes nothing;
# step 2 (`confirm` present) cancels. Both look the booking up again.
@public_property_view
@require_POST
def cancel(request):
    booking, response = _lookup(request)
    if response is not None:
        return response
    if 'confirm' in request.POST:
        return _cancel(request, booking)
    if booking.status not in CANCELLABLE_STATUSES:
        # The detail page has no cancel button for these; this is a hand-made POST
        error = None if booking.status == Booking.Status.CANCELLED else NOT_CANCELLABLE_MESSAGE
        return _render_detail(request, booking, error=error)
    return _render_cancel_confirm(request, booking)
