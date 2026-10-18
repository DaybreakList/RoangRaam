from datetime import date
from decimal import Decimal

import factory
import pytest
from django.core import mail
from django.http import Http404

from apps.bookings.emails import LOGO_CID, send_booking_confirmation
from apps.bookings.tests.factories import (
    BookingFactory,
    BookingNightRateFactory,
    BookingRoomFactory,
    GuestFactory,
)
from apps.properties.tests.factories import PropertyFactory
from apps.rooms.tests.factories import RoomFactory


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def make_booking(prop):
    """Two nights in room 101 at 1,200 then 1,500 baht."""
    booking = BookingFactory(
        property=prop,
        guest=GuestFactory(property=prop, first_name='Somchai', last_name='Jaidee', email='somchai@example.com'),
        check_in_date=date(2026, 11, 1),
        check_out_date=date(2026, 11, 3),
        total_amount=Decimal('2700.00'),
    )
    booking_room = BookingRoomFactory(
        booking=booking,
        room=RoomFactory(property=prop, number='101'),
        subtotal=Decimal('2700.00'),
    )
    BookingNightRateFactory(booking_room=booking_room, night=date(2026, 11, 1), price=Decimal('1200.00'))
    BookingNightRateFactory(booking_room=booking_room, night=date(2026, 11, 2), price=Decimal('1500.00'))
    return booking


@pytest.mark.django_db
class TestSendBookingConfirmation:
    def test_sends_booking_details_to_the_guest(self):
        prop = PropertyFactory(name='Riverside', logo=factory.django.ImageField(filename='logo.png'))
        booking = make_booking(prop)

        send_booking_confirmation(prop, booking.pk)

        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert sent.to == ['somchai@example.com']
        assert booking.code in sent.subject and 'Riverside' in sent.subject
        html = sent.alternatives[0].content
        for body in (sent.body, html):
            assert booking.code in body
            assert 'Somchai' in body
            assert '101' in body
            assert '1,200.00' in body and '1,500.00' in body
            assert '2,700.00' in body

    def test_logo_is_attached_inline(self):
        prop = PropertyFactory(logo=factory.django.ImageField(filename='logo.png'))
        booking = make_booking(prop)

        send_booking_confirmation(prop, booking.pk)

        sent = mail.outbox[0]
        assert f'src="cid:{LOGO_CID}"' in sent.alternatives[0].content
        message = sent.message()
        logo = next(part for part in message.walk() if part.get('Content-ID') == f'<{LOGO_CID}>')
        assert logo.get_content_type() == 'image/png'
        assert logo.get_content_disposition() == 'inline'

    def test_property_without_logo_sends_without_image(self):
        prop = PropertyFactory()
        booking = make_booking(prop)

        send_booking_confirmation(prop, booking.pk)

        sent = mail.outbox[0]
        assert 'cid:' not in sent.alternatives[0].content
        assert sent.attachments == []

    def test_names_are_escaped_in_html_but_not_in_text(self):
        prop = PropertyFactory(name='Tom & Jerry <Inn>')
        booking = make_booking(prop)

        send_booking_confirmation(prop, booking.pk)

        sent = mail.outbox[0]
        assert 'Tom & Jerry <Inn>' in sent.body
        assert 'Tom &amp; Jerry &lt;Inn&gt;' in sent.alternatives[0].content
        assert '<Inn>' not in sent.alternatives[0].content

    def test_other_property_cannot_send(self, hotel_a, hotel_b):
        booking = make_booking(hotel_a)

        with pytest.raises(Http404):
            send_booking_confirmation(hotel_b, booking.pk)

        assert mail.outbox == []