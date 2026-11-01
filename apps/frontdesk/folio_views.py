"""
Folio page at the front desk (TASK-48): what the guest owes, taking the payment
and closing the bill. The rules live in apps/bookings/folio.py.
"""

import uuid

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from apps.bookings.folio import close_bill, get_folio, record_desk_payment
from apps.bookings.models import Booking
from apps.bookings.receipt_pdf import receipt_data, render_receipt_pdf
from apps.properties.permissions import Role, role_required

# Same access as check-in / check-out
FRONT_DESK_ROLES = (Role.OWNER, Role.MANAGER, Role.FRONT_DESK)


def _scoped_booking(request, booking_id):
    return get_object_or_404(
        Booking.scoped.for_property(request.property).select_related('guest'), pk=booking_id,
    )


def _back_to_folio(request, booking_id):
    return redirect('folio:detail', property_slug=request.property.slug, booking_id=booking_id)


def _show_errors(request, error):
    for message in error.messages:
        messages.error(request, message)


@require_GET
@role_required(*FRONT_DESK_ROLES)
def folio_view(request, booking_id):
    booking = _scoped_booking(request, booking_id)
    folio = get_folio(request.property, booking)
    is_checked_in = booking.status == Booking.Status.CHECKED_IN
    return render(request, 'frontdesk/folio.html', {
        'booking': booking,
        'folio': folio,
        'can_pay': is_checked_in and not folio.is_closed and folio.balance > 0,
        'can_close': is_checked_in and not folio.is_closed and folio.balance == 0,
        # New key on every render: a double submit of this form is recorded once
        'payment_key': uuid.uuid4(),
    })


@require_POST
@role_required(*FRONT_DESK_ROLES)
def pay_view(request, booking_id):
    try:
        payment = record_desk_payment(
            request.property, booking_id,
            user=request.user, idempotency_key=request.POST.get('idempotency_key', ''),
        )
    except ValidationError as error:
        _show_errors(request, error)
    else:
        messages.success(request, f'รับชำระ {payment.amount:,.2f} บาท เรียบร้อยแล้ว')
    return _back_to_folio(request, booking_id)


@require_POST
@role_required(*FRONT_DESK_ROLES)
def close_view(request, booking_id):
    try:
        receipt = close_bill(request.property, booking_id, user=request.user)
    except ValidationError as error:
        _show_errors(request, error)
    else:
        messages.success(request, f'ปิดบิลแล้ว ใบเสร็จเลขที่ {receipt.number}')
    return _back_to_folio(request, booking_id)


@require_GET
@role_required(*FRONT_DESK_ROLES)
def receipt_view(request, booking_id):
    """The receipt of a closed bill as a PDF, built for this request only (TASK-49)."""
    booking = _scoped_booking(request, booking_id)
    data = receipt_data(request.property, booking)
    if data is None:
        # No receipt until the bill is closed
        raise Http404
    response = HttpResponse(render_receipt_pdf(data), content_type='application/pdf')
    response['Content-Disposition'] = f'inline; filename="receipt-{data.number}-{data.booking_code}.pdf"'
    # Holds the guest's details: keep it out of shared and browser caches
    response['Cache-Control'] = 'private, no-store'
    return response
