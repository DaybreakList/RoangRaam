from email.mime.image import MIMEImage
from pathlib import Path

from django.core.mail import EmailMultiAlternatives
from django.http import Http404
from django.template.loader import render_to_string

from .models import Booking
from .services import booking_with_details

# Content-ID of the inline logo; the HTML template points to it as cid:hotel-logo
LOGO_CID = 'hotel-logo'


def send_booking_confirmation(property, booking_id):
    """
    Email the guest the confirmation: hotel name and logo, booking code, dates,
    rooms and nightly prices.

    Sent synchronously. The logo is attached inline instead of linked, so it
    shows even when the site is not reachable from the guest's mail client.
    Raises Http404 when the booking belongs to another property.
    """
    try:
        booking = booking_with_details(property, booking_id)
    except Booking.DoesNotExist:
        raise Http404

    context = {'property': property, 'booking': booking, 'logo_cid': LOGO_CID if property.logo else None}
    message = EmailMultiAlternatives(
        subject=f'ยืนยันการจอง {booking.code} - {property.name}',
        body=render_to_string('bookings/email/confirmation.txt', context),
        to=[booking.guest.email],
    )
    message.attach_alternative(render_to_string('bookings/email/confirmation.html', context), 'text/html')
    if property.logo:
        # "related" keeps the logo inside the HTML part instead of as a download
        message.mixed_subtype = 'related'
        message.attach(_logo_image(property.logo))
    message.send()
    return message


def _logo_image(logo):
    with logo.open('rb') as file:
        data = file.read()
    filename = Path(logo.name).name
    subtype = Path(filename).suffix.lstrip('.').lower().replace('jpg', 'jpeg')
    image = MIMEImage(data, _subtype=subtype)
    image.add_header('Content-ID', f'<{LOGO_CID}>')
    image.add_header('Content-Disposition', 'inline', filename=filename)
    return image