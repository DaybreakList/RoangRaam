import io
import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from apps.bookings.folio import close_bill, record_desk_payment
from apps.bookings.models import Booking
from apps.bookings.receipt_pdf import build_receipt_pdf, receipt_data, render_receipt_pdf
from apps.bookings.tests.factories import BookingFactory, BookingNightRateFactory, BookingRoomFactory
from apps.rooms.tests.factories import RoomFactory

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / 'media'
    return settings.MEDIA_ROOT


def closed_booking(hotel, price='1070.00', nights=2):
    booking = BookingFactory(
        property=hotel, status=Booking.Status.CHECKED_IN, payment_option=Booking.PaymentOption.PAY_AT_HOTEL,
    )
    booking.check_out_date = booking.check_in_date + timedelta(days=nights)
    booking.total_amount = Decimal(price) * nights
    booking.save()
    booking_room = BookingRoomFactory(booking=booking, room=RoomFactory(property=hotel, number='501'))
    for offset in range(nights):
        BookingNightRateFactory(
            booking_room=booking_room, night=booking.check_in_date + timedelta(days=offset), price=Decimal(price),
        )
    record_desk_payment(hotel, booking.pk, user=None, idempotency_key=uuid.uuid4())
    close_bill(hotel, booking.pk, user=None)
    return Booking.scoped.for_property(hotel).select_related('guest').get(pk=booking.pk)


def png_logo():
    buffer = io.BytesIO()
    Image.new('RGBA', (40, 40), (18, 105, 93, 255)).save(buffer, format='PNG')
    return SimpleUploadedFile('logo.png', buffer.getvalue(), content_type='image/png')


def test_data_comes_from_the_receipt(hotel_a):
    booking = closed_booking(hotel_a)

    data = receipt_data(hotel_a, booking)

    assert data.number == 1
    assert data.booking_code == booking.code
    assert data.guest_name == f'{booking.guest.first_name} {booking.guest.last_name}'
    assert data.nights == 2
    assert [line.room_number for line in data.lines] == ['501', '501']
    assert data.total == Decimal('2140.00')
    assert data.tax_rate == Decimal('7.00')
    assert data.tax_amount == Decimal('140.00')
    assert data.net_amount == Decimal('2000.00')
    assert data.paid == Decimal('2140.00')


def test_a_later_tax_change_does_not_change_the_receipt(hotel_a):
    booking = closed_booking(hotel_a)
    hotel_a.tax_rate = Decimal('10.00')
    hotel_a.save()

    data = receipt_data(hotel_a, booking)

    assert data.tax_rate == Decimal('7.00')
    assert data.tax_amount == Decimal('140.00')


def test_issued_time_is_in_the_hotel_time_zone(hotel_a):
    booking = closed_booking(hotel_a)

    data = receipt_data(hotel_a, booking)

    assert str(data.issued_at.tzinfo) == hotel_a.timezone


def test_open_bill_has_no_receipt(hotel_a):
    booking = BookingFactory(property=hotel_a, status=Booking.Status.CHECKED_IN)

    assert receipt_data(hotel_a, booking) is None


def test_booking_of_another_hotel_is_refused(hotel_a, hotel_b):
    booking = closed_booking(hotel_b)

    with pytest.raises(ValueError):
        receipt_data(hotel_a, booking)


def test_pdf_is_built_with_thai_text_shaping(hotel_a):
    data = receipt_data(hotel_a, closed_booking(hotel_a))

    pdf = build_receipt_pdf(data)

    # Without shaping, Thai vowels and tone marks above and below letters are lost
    assert pdf.text_shaping
    assert set(pdf.fonts) >= {'sarabun', 'sarabunB'}


def test_pdf_bytes(hotel_a):
    content = render_receipt_pdf(receipt_data(hotel_a, closed_booking(hotel_a)))

    assert content.startswith(b'%PDF-')
    assert content.rstrip().endswith(b'%%EOF')


def test_pdf_with_a_logo(hotel_a):
    hotel_a.logo = png_logo()
    hotel_a.save()
    data = receipt_data(hotel_a, closed_booking(hotel_a))

    assert data.logo
    assert render_receipt_pdf(data).startswith(b'%PDF-')


def test_a_broken_logo_does_not_stop_the_receipt(hotel_a):
    hotel_a.logo = SimpleUploadedFile('logo.png', b'not an image', content_type='image/png')
    hotel_a.save()
    data = receipt_data(hotel_a, closed_booking(hotel_a))

    assert render_receipt_pdf(data).startswith(b'%PDF-')


def test_a_missing_logo_file_is_skipped(hotel_a, media_root):
    hotel_a.logo = png_logo()
    hotel_a.save()
    (media_root / hotel_a.logo.name).unlink()

    assert receipt_data(hotel_a, closed_booking(hotel_a)).logo is None


def test_a_long_stay_spans_pages(hotel_a):
    data = receipt_data(hotel_a, closed_booking(hotel_a, nights=60))

    assert build_receipt_pdf(data).pages_count >= 2


def test_nothing_is_written_to_media_root(hotel_a, media_root):
    render_receipt_pdf(receipt_data(hotel_a, closed_booking(hotel_a)))

    assert not media_root.exists() or not any(media_root.rglob('*.pdf'))
