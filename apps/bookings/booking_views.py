"""
Guest booking flow after the search page: choose rooms -> guest details -> confirm,
then Stripe (FULL) or the confirmation page (PAY_AT_HOTEL).

Business rules live in services.py; these views read the request, call the service
and pick the next page.
"""

import logging
import uuid
from urllib.parse import urlencode

import stripe
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_safe

from apps.properties.permissions import public_property_view

from .emails import send_booking_confirmation_safely
from .forms import STALE_FORM_MESSAGE, BookingForm, SearchForm, read_room_quantities
from .models import Booking
from .payments import checkout_is_open, start_checkout
from .services import (
    MAX_ROOMS_PER_BOOKING,
    TOO_MANY_ROOMS_MESSAGE,
    booking_with_details,
    get_available_room_types,
    get_or_create_booking,
)

logger = logging.getLogger(__name__)

NO_ROOMS_MESSAGE = 'กรุณาเลือกห้องอย่างน้อย 1 ห้อง'
# One message for another hotel's id, an id that does not exist and a sold-out type,
# so the page never tells which of the three it was
ROOM_UNAVAILABLE_MESSAGE = 'ห้องที่เลือกไม่พร้อมให้จองแล้ว กรุณาค้นหาห้องว่างอีกครั้ง'
INVALID_DATES_MESSAGE = 'ข้อมูลวันที่เข้าพักไม่ถูกต้อง กรุณาค้นหาห้องว่างอีกครั้ง'
PAYMENT_ERROR_MESSAGE = 'ระบบชำระเงินขัดข้องชั่วคราว กรุณาลองใหม่อีกครั้ง'
EMAIL_SENT_MESSAGE = 'ส่งอีเมลยืนยันไปที่อีเมลที่คุณกรอกแล้ว'
EMAIL_FAILED_MESSAGE = 'ส่งอีเมลยืนยันไม่สำเร็จ กรุณาจดรหัสการจองไว้'

GUEST_FIELDS = ('first_name', 'last_name', 'email', 'phone')


class SelectionError(Exception):
    """The chosen rooms cannot be booked as asked; `message` is shown to the guest."""

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def _search_url(property, check_in=None, check_out=None, guests=None):
    """Link back to the search page, with the search filled in when it is all known."""
    url = reverse('bookings:search', kwargs={'property_slug': property.slug})
    if check_in and check_out and guests:
        query = {'check_in': check_in.isoformat(), 'check_out': check_out.isoformat(), 'guests': guests}
        return f'{url}?{urlencode(query)}'
    return url


def _load_selection(property, quantities, check_in, check_out):
    """
    Pair each chosen quantity with its room type, from the property's own free types.

    Raises SelectionError when nothing is chosen, too many rooms are chosen, a type
    is not on offer for these dates, or a quantity is more than what is free. Prices
    are the service's per-night sums; the estimate only multiplies them by quantity.
    """
    if not quantities:
        raise SelectionError(NO_ROOMS_MESSAGE)
    if sum(quantities.values()) > MAX_ROOMS_PER_BOOKING:
        raise SelectionError(TOO_MANY_ROOMS_MESSAGE)
    try:
        available = {rt.pk: rt for rt in get_available_room_types(property, check_in, check_out)}
    except ValidationError as error:
        raise SelectionError(' '.join(error.messages)) from error
    if any(room_type_id not in available for room_type_id in quantities):
        raise SelectionError(ROOM_UNAVAILABLE_MESSAGE)
    lines = []
    for room_type_id, quantity in sorted(quantities.items()):
        room_type = available[room_type_id]
        if quantity > room_type.available_count:
            raise SelectionError(f'ห้องประเภท {room_type.name} เหลือ {room_type.available_count} ห้อง')
        lines.append({
            'room_type': room_type,
            'quantity': quantity,
            'subtotal': room_type.total_price * quantity,
        })
    return lines


def _render_unavailable(request, error_messages, back_url):
    """The page that replaces the form when the choice cannot be booked. Nothing is created."""
    context = {'error_messages': error_messages, 'back_url': back_url}
    return render(request, 'bookings/book.html', context)


def _render_form(request, form, lines, check_in, check_out, back_url):
    context = {
        'form': form,
        'lines': lines,
        'nights': (check_out - check_in).days,
        'estimated_total': sum((line['subtotal'] for line in lines), start=0),
        'check_in': check_in,
        'check_out': check_out,
        'back_url': back_url,
    }
    return render(request, 'bookings/book.html', context)


def _form_messages(form):
    return [str(error) for errors in form.errors.values() for error in errors]


def _show_book_page(request):
    search_form = SearchForm(request.GET)
    plain_back_url = _search_url(request.property)
    if not search_form.is_valid():
        return _render_unavailable(request, _form_messages(search_form), plain_back_url)
    data = search_form.cleaned_data
    check_in, check_out, guests = data['check_in'], data['check_out'], data['guests']
    back_url = _search_url(request.property, check_in, check_out, guests)

    try:
        quantities = read_room_quantities(request.GET)
        lines = _load_selection(request.property, quantities, check_in, check_out)
    except ValidationError as error:
        return _render_unavailable(request, error.messages, back_url)
    except SelectionError as error:
        return _render_unavailable(request, [error.message], back_url)

    form = BookingForm(initial={
        'check_in': check_in,
        'check_out': check_out,
        'guests': guests,
        'adults': guests,
        'children': 0,
        'idempotency_key': uuid.uuid4(),
    })
    return _render_form(request, form, lines, check_in, check_out, back_url)


def _add_service_errors(form, error):
    """Put a service ValidationError on the form: known field names go to that field."""
    if hasattr(error, 'error_dict'):
        for key, errors in error.error_dict.items():
            form.add_error(key if key in form.fields else None, errors)
    else:
        form.add_error(None, error)


def _with_fresh_key(request):
    """Rebuild a form whose idempotency key was missing or damaged, with a new key."""
    data = request.POST.copy()
    data['idempotency_key'] = str(uuid.uuid4())
    fresh = BookingForm(data)
    fresh.is_valid()
    fresh.add_error(None, STALE_FORM_MESSAGE)
    return fresh


def _booking_confirmed_url(property, booking_id):
    return reverse(
        'bookings:booking-confirmed',
        kwargs={'property_slug': property.slug, 'booking_id': booking_id},
    )


def _render_payment_failure(request, booking):
    # Without checkout_open the page treats a fresh booking as past its Checkout deadline
    context = {
        'booking': booking,
        'error': PAYMENT_ERROR_MESSAGE,
        'checkout_open': checkout_is_open(booking),
    }
    return render(request, 'bookings/payment_result.html', context, status=502)


def _continue_after_booking(request, booking, created):
    """
    Pick the next page from what the service stored, not from what the form said:
    a repeated submit with the same key must go the same way as the first one.
    """
    property = request.property
    if booking.payment_option == Booking.PaymentOption.PAY_AT_HOTEL:
        if created:
            # Once only: a repeat of the same submit does not send a second email
            if send_booking_confirmation_safely(property, booking.pk):
                messages.success(request, EMAIL_SENT_MESSAGE)
            else:
                messages.warning(request, EMAIL_FAILED_MESSAGE)
        return redirect(_booking_confirmed_url(property, booking.pk))

    # FULL: the webhook confirms the booking and sends the email after payment
    result_url = request.build_absolute_uri(reverse(
        'bookings:payment-result',
        kwargs={'property_slug': property.slug, 'booking_id': booking.pk},
    ))
    try:
        checkout_url = start_checkout(property, booking.pk, result_url, result_url)
    except ValidationError:
        # The result page explains the booking's status (paid, expired, ...)
        return redirect(result_url)
    except stripe.StripeError:
        logger.exception('Stripe Checkout failed for booking %s', booking.pk)
        return _render_payment_failure(request, booking)
    return redirect(checkout_url)


def _submit_booking(request):
    form = BookingForm(request.POST)
    form.is_valid()
    cleaned = form.cleaned_data
    check_in, check_out = cleaned.get('check_in'), cleaned.get('check_out')
    plain_back_url = _search_url(request.property)
    if check_in is None or check_out is None:
        return _render_unavailable(request, [INVALID_DATES_MESSAGE], plain_back_url)
    back_url = _search_url(request.property, check_in, check_out, cleaned.get('guests'))

    try:
        quantities = read_room_quantities(request.POST)
    except ValidationError as error:
        return _render_unavailable(request, error.messages, back_url)

    if not form.errors:
        try:
            booking, created = get_or_create_booking(
                request.property,
                check_in=check_in,
                check_out=check_out,
                rooms=[
                    {'room_type_id': room_type_id, 'quantity': quantity}
                    for room_type_id, quantity in quantities.items()
                ],
                guest={name: cleaned[name] for name in GUEST_FIELDS},
                adults=cleaned['adults'],
                children=cleaned['children'],
                payment_option=cleaned['payment_option'],
                idempotency_key=cleaned['idempotency_key'],
            )
        except ValidationError as error:
            # An Http404 (another hotel's room type id) is not caught: it stays a 404
            _add_service_errors(form, error)
            service_messages = error.messages
        else:
            return _continue_after_booking(request, booking, created)
    else:
        service_messages = None

    # Show the form again only while the chosen rooms can still be booked
    try:
        lines = _load_selection(request.property, quantities, check_in, check_out)
    except SelectionError as error:
        return _render_unavailable(request, service_messages or [error.message], back_url)
    if 'idempotency_key' in form.errors:
        form = _with_fresh_key(request)
    return _render_form(request, form, lines, check_in, check_out, back_url)


# GET shows the form, POST creates the booking; POST is protected by CSRF
@public_property_view
@require_http_methods(['GET', 'POST'])
def book(request):
    if request.method == 'POST':
        return _submit_booking(request)
    return _show_book_page(request)


def _room_lines(booking):
    """[(room type name, count)] in the order the rooms are listed; never the room numbers."""
    counts = {}
    for booking_room in booking.booking_rooms.all():
        name, count = counts.get(booking_room.room_type_id, (booking_room.room_type.name, 0))
        counts[booking_room.room_type_id] = (name, count + 1)
    return list(counts.values())


# Opened with the booking's UUID alone, so it never shows who the guest is
@public_property_view
@require_safe
def booking_confirmed(request, booking_id):
    try:
        booking = booking_with_details(request.property, booking_id)
    except Booking.DoesNotExist:
        raise Http404 from None
    if booking.payment_option == Booking.PaymentOption.FULL:
        return redirect(reverse(
            'bookings:payment-result',
            kwargs={'property_slug': request.property.slug, 'booking_id': booking.pk},
        ))
    context = {'booking': booking, 'room_lines': _room_lines(booking)}
    return render(request, 'bookings/booking_confirmed.html', context)
