"""
The check-in form, the check-out button and the messages on the "Today" page.
The POST endpoints themselves are tested in test_check_in_out_views.py.
"""

import re
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.contrib.messages import constants
from django.contrib.messages.storage.base import Message
from django.db import connection
from django.template import Context, Template
from django.template.loader import render_to_string
from django.test import RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.bookings.folio import close_bill, record_desk_payment
from apps.bookings.models import Booking
from apps.bookings.tests.factories import BookingFactory, BookingRoomFactory, GuestFactory
from apps.frontdesk.services.daily_ops import property_today
from apps.properties.models import Membership
from apps.properties.tests.factories import PropertyFactory
from apps.rooms.models import Room
from apps.rooms.tests.factories import RoomFactory

pytestmark = pytest.mark.django_db

Status = Booking.Status
Role = Membership.Role
CLEAN = Room.HousekeepingStatus.CLEAN
DIRTY = Room.HousekeepingStatus.DIRTY
ID = {'id_type': 'PASSPORT', 'id_number': 'AA1234567', 'nationality': 'Japan'}
EARLY_WARNING = 'ไม่คืนเงินสำหรับคืนที่เหลือ'
TOO_EARLY_NOTE = 'เช็คเอาต์ได้ตั้งแต่วันถัดไป'
BILL_NOTE = 'ต้องรับชำระและปิดบิลก่อน'


def today_url(hotel):
    return reverse('daily_ops:index', kwargs={'property_slug': hotel.slug})


def action_url(name, booking, hotel=None):
    return reverse(f'check_in_out:{name}', kwargs={
        'property_slug': (hotel or booking.property).slug,
        'booking_id': booking.pk,
    })


def book(hotel, start, end, status=Status.CONFIRMED, rooms=({},), **extra):
    """A booking of `hotel` with one room per dict in `rooms`; days are offsets from the hotel's today."""
    today = property_today(hotel)
    booking = BookingFactory(
        property=hotel, status=status,
        check_in_date=today + timedelta(days=start), check_out_date=today + timedelta(days=end),
        **extra,
    )
    for room_kwargs in rooms:
        room = RoomFactory(property=hotel, **{'hk_status': CLEAN, **room_kwargs})
        BookingRoomFactory(booking=booking, room=room)
    return booking


def get_page(client, hotel):
    return client.get(today_url(hotel)).content.decode()


def form_for(page, url):
    """The <form> that posts to `url`, or None."""
    match = re.search(rf'<form[^>]*action="{re.escape(url)}".*?</form>', page, re.S)
    return match.group(0) if match else None


def forms_to(page, url):
    return page.count(f'action="{url}"')


def close_the_bill(booking):
    """Close the bill of a CHECKED_IN `booking` for real, so the Today page shows its check-out form."""
    hotel = booking.property
    if booking.total_amount > 0:
        record_desk_payment(hotel, booking.pk, user=None, idempotency_key=uuid.uuid4())
    close_bill(hotel, booking.pk, user=None)


# ---------- Check-in form ----------

def test_check_in_form_is_only_on_confirmed_arrivals(client, hotel_a, login_member):
    confirmed = book(hotel_a, 0, 2)
    arrived = book(hotel_a, 0, 2, Status.CHECKED_IN)
    left = book(hotel_a, -2, 0, Status.CHECKED_OUT)
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    assert forms_to(page, action_url('check_in', confirmed)) == 1
    assert forms_to(page, action_url('check_in', arrived)) == 0
    assert forms_to(page, action_url('check_in', left)) == 0
    # A guest who is in or gone has no check-out form here either: it is the check-in day / already out
    assert forms_to(page, action_url('check_out', arrived)) == 0
    assert forms_to(page, action_url('check_out', left)) == 0


def test_check_in_form_posts_to_its_own_booking_with_the_form_fields(client, hotel_a, login_member):
    first = book(hotel_a, 0, 2)
    second = book(hotel_a, 0, 3)
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    for booking in (first, second):
        form = form_for(page, action_url('check_in', booking))
        assert form is not None
        assert 'method="post"' in form
        assert 'csrfmiddlewaretoken' in form
        for name in ('id_type', 'id_number', 'nationality'):
            assert f'name="{name}"' in form
        # Both ID types come from Guest.IdType through CheckInForm
        assert 'value="ID_CARD"' in form and 'value="PASSPORT"' in form
        assert 'ยืนยันเช็คอิน' in form


def test_ids_stay_unique_with_many_rows(client, hotel_a, login_member):
    for _ in range(3):
        book(hotel_a, 0, 2)
    book(hotel_a, -2, 0, Status.CHECKED_IN)  # in both "departures" and "in house"
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    ids = [found for found in re.findall(r'\sid="([^"]+)"', page) if found.startswith(('ci-', 'co-'))]
    assert len(ids) > 10
    assert len(ids) == len(set(ids))


def test_the_form_never_prefills_the_saved_id_of_a_returning_guest(client, hotel_a, login_member):
    guest = GuestFactory(property=hotel_a, id_type='ID_CARD', id_number='1101700000000', nationality='Thai')
    book(hotel_a, 0, 2, guest=guest)
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    assert '1101700000000' not in page


def test_a_clean_multi_room_booking_lists_every_room_in_the_form(client, hotel_a, login_member):
    booking = book(hotel_a, 0, 2, rooms=({'number': '301'}, {'number': '302'}))
    login_member(hotel_a)

    form = form_for(get_page(client, hotel_a), action_url('check_in', booking))

    assert form is not None
    assert 'ห้อง 301' in form and 'ห้อง 302' in form
    assert form.count('สะอาด') >= 2


@pytest.mark.parametrize('room_kwargs, reason', [
    ({'hk_status': DIRTY}, 'ยังไม่ได้ทำความสะอาด'),
    ({'is_active': False}, 'ปิดใช้งานอยู่'),
])
def test_a_room_that_is_not_ready_disables_the_button_and_says_why(client, hotel_a, login_member,
                                                                    room_kwargs, reason):
    booking = book(hotel_a, 0, 2, rooms=({'number': '401', **room_kwargs},))
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    assert forms_to(page, action_url('check_in', booking)) == 0
    assert f'ห้อง 401 {reason}' in page
    assert re.search(r'<button type="button" class="rr-btn ci-btn" disabled', page)
    room_status = reverse('room_status:index', kwargs={'property_slug': hotel_a.slug})
    assert f'href="{room_status}"' in page


def test_one_room_that_is_not_ready_blocks_the_whole_booking(client, hotel_a, login_member):
    booking = book(hotel_a, 0, 2, rooms=({'number': '501'}, {'number': '502', 'hk_status': DIRTY}))
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    assert forms_to(page, action_url('check_in', booking)) == 0
    assert 'ห้อง 502 ยังไม่ได้ทำความสะอาด' in page
    assert 'ห้อง 501 ยังไม่ได้ทำความสะอาด' not in page


# ---------- Check-out button ----------

def test_check_out_button_is_on_checked_in_rows_of_both_sections(client, hotel_a, login_member):
    # Leaves today: listed under "departures" and "in house", so the button shows twice
    leaving = book(hotel_a, -2, 0, Status.CHECKED_IN)
    staying = book(hotel_a, -1, 2, Status.CHECKED_IN)
    left = book(hotel_a, -2, 0, Status.CHECKED_OUT)
    confirmed = book(hotel_a, 0, 2)
    # bill must be closed for the check-out form (bill_closed)
    close_the_bill(leaving)
    close_the_bill(staying)
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    assert forms_to(page, action_url('check_out', leaving)) == 2
    assert forms_to(page, action_url('check_out', staying)) == 1
    assert forms_to(page, action_url('check_out', left)) == 0
    assert forms_to(page, action_url('check_out', confirmed)) == 0
    folio = reverse('folio:detail', kwargs={'property_slug': hotel_a.slug, 'booking_id': leaving.pk})
    assert page.count(f'href="{folio}"') >= 3  # the code link twice plus the "pay / close bill" link


def test_check_out_is_disabled_on_the_check_in_day(client, hotel_a, login_member):
    booking = book(hotel_a, 0, 2, Status.CHECKED_IN)
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    assert forms_to(page, action_url('check_out', booking)) == 0
    assert TOO_EARLY_NOTE in page


def test_an_open_bill_disables_check_out_on_the_page_but_keeps_the_folio_link(client, hotel_a, login_member):
    booking = book(hotel_a, -1, 2, Status.CHECKED_IN)
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    assert forms_to(page, action_url('check_out', booking)) == 0
    assert BILL_NOTE in page
    assert TOO_EARLY_NOTE not in page
    assert re.search(r'<button type="button" class="rr-btn ci-btn" disabled', page)
    assert 'รับชำระ / ปิดบิล' in page
    assert page.count(f'href="{folio_link(hotel_a, booking)}"') >= 2  # the code link and the "pay / close bill" link


def test_a_closed_bill_gives_the_check_out_form_and_only_its_own_row(client, hotel_a, login_member):
    closed = book(hotel_a, -1, 2, Status.CHECKED_IN)
    open_bill = book(hotel_a, -1, 3, Status.CHECKED_IN)
    close_the_bill(closed)
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    assert forms_to(page, action_url('check_out', closed)) == 1
    assert forms_to(page, action_url('check_out', open_bill)) == 0
    assert page.count(BILL_NOTE) == 1


def test_the_check_in_day_note_wins_over_an_open_bill_on_the_page(client, hotel_a, login_member):
    booking = book(hotel_a, 0, 2, Status.CHECKED_IN)
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    assert forms_to(page, action_url('check_out', booking)) == 0
    assert TOO_EARLY_NOTE in page
    assert BILL_NOTE not in page


def test_leaving_early_asks_first_and_says_the_rest_is_not_refunded(client, hotel_a, login_member):
    booking = book(hotel_a, -1, 3, Status.CHECKED_IN)
    # bill must be closed for the check-out form (bill_closed)
    close_the_bill(booking)
    login_member(hotel_a)

    form = form_for(get_page(client, hotel_a), action_url('check_out', booking))

    assert form is not None
    assert 'confirm(' in form
    assert 'ออกก่อนกำหนด' in form
    assert EARLY_WARNING in form


@pytest.mark.parametrize('check_in, check_out', [(-2, 0), (-3, -1)], ids=['leaves-today', 'overdue'])
def test_no_early_warning_on_or_after_the_check_out_date(client, hotel_a, login_member, check_in, check_out):
    booking = book(hotel_a, check_in, check_out, Status.CHECKED_IN)
    # bill must be closed for the check-out form (bill_closed)
    close_the_bill(booking)
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    form = form_for(page, action_url('check_out', booking))
    assert form is not None and 'confirm(' in form
    assert EARLY_WARNING not in page


def test_guest_names_are_escaped_inside_the_confirm_text(client, hotel_a, login_member):
    guest = GuestFactory(property=hotel_a, first_name='''O'Brien "x"''', last_name='</script>')
    booking = book(hotel_a, -1, 3, Status.CHECKED_IN, guest=guest)
    # bill must be closed for the check-out form (bill_closed)
    close_the_bill(booking)
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    form = form_for(page, action_url('check_out', booking))
    assert 'O\\u0027Brien \\u0022x\\u0022' in form
    assert '\\u003C/script\\u003E' in form
    assert "O'Brien" not in page
    assert '</script>' not in form


def test_today_comes_from_the_hotel_timezone_not_the_server_clock(client, login_member):
    # 20:00 UTC on 2 November is already 3 November in Bangkok
    moment = datetime(2026, 11, 2, 20, 0, tzinfo=dt_timezone.utc)
    bangkok = PropertyFactory(timezone='Asia/Bangkok')
    login_member(bangkok)
    checked_in_today = BookingFactory(
        property=bangkok, status=Status.CHECKED_IN,
        check_in_date=date(2026, 11, 3), check_out_date=date(2026, 11, 5),
    )
    checked_in_yesterday = BookingFactory(
        property=bangkok, status=Status.CHECKED_IN,
        check_in_date=date(2026, 11, 2), check_out_date=date(2026, 11, 5),
    )
    for booking in (checked_in_today, checked_in_yesterday):
        BookingRoomFactory(booking=booking, room=RoomFactory(property=bangkok, hk_status=CLEAN))
    # bill must be closed for the check-out form (bill_closed)
    close_the_bill(checked_in_yesterday)

    with patch('django.utils.timezone.now', return_value=moment):
        page = get_page(client, bangkok)

    assert forms_to(page, action_url('check_out', checked_in_today)) == 0
    assert forms_to(page, action_url('check_out', checked_in_yesterday)) == 1


# ---------- Check-out button rendered by itself (the Folio page has a booking, no row) ----------

def render_check_out(hotel, booking, args='booking today "folio"'):
    """The tag rendered straight from a Template, like the Folio page will call it."""
    request = RequestFactory().get('/')
    request.property = hotel
    template = Template('{% load check_in_out_ui %}{% check_out_button ' + args + ' %}')
    return template.render(Context({
        'request': request,
        'csrf_token': 'test-token',
        'booking': booking,
        'today': property_today(hotel),
    }))


def folio_link(hotel, booking):
    return reverse('folio:detail', kwargs={'property_slug': hotel.slug, 'booking_id': booking.pk})


def dom_ids(html):
    return re.findall(r'\sid="([^"]+)"', html)


def test_the_tag_renders_from_a_booking_without_a_row(hotel_a):
    booking = book(hotel_a, -1, 2, Status.CHECKED_IN)

    html = render_check_out(hotel_a, booking)

    form = form_for(html, action_url('check_out', booking))
    assert form is not None
    assert 'csrfmiddlewaretoken' in form
    assert 'confirm(' in form


def test_the_folio_link_is_shown_by_default_and_hidden_on_request(hotel_a):
    booking = book(hotel_a, -1, 2, Status.CHECKED_IN)
    link = f'href="{folio_link(hotel_a, booking)}"'

    assert link in render_check_out(hotel_a, booking)
    assert link in render_check_out(hotel_a, booking, 'booking today "folio" show_folio_link=True')
    hidden = render_check_out(hotel_a, booking, 'booking today "folio" show_folio_link=False')
    assert folio_link(hotel_a, booking) not in hidden
    assert 'รับชำระ / ปิดบิล' not in hidden
    assert forms_to(hidden, action_url('check_out', booking)) == 1  # the button is unaffected


def test_an_open_bill_disables_the_button_and_leaves_no_form_to_submit(hotel_a):
    booking = book(hotel_a, -1, 2, Status.CHECKED_IN)

    html = render_check_out(hotel_a, booking, 'booking today "folio" bill_closed=False')

    # No <form> at all, so Enter cannot post it either
    assert '<form' not in html
    assert forms_to(html, action_url('check_out', booking)) == 0
    assert re.search(r'<button type="button" class="rr-btn ci-btn" disabled', html)
    assert 'type="submit"' not in html
    assert BILL_NOTE in html


def test_an_open_bill_links_to_the_folio_next_to_the_reason_when_asked(hotel_a):
    booking = book(hotel_a, -1, 2, Status.CHECKED_IN)

    shown = render_check_out(hotel_a, booking, 'booking today "x" bill_closed=False')
    hidden = render_check_out(hotel_a, booking, 'booking today "x" bill_closed=False show_folio_link=False')

    assert f'href="{folio_link(hotel_a, booking)}"' in shown
    assert BILL_NOTE in shown
    assert folio_link(hotel_a, booking) not in hidden
    assert BILL_NOTE in hidden


@pytest.mark.parametrize('arg', ['bill_closed=True', 'bill_closed=None', ''], ids=['closed', 'unknown', 'default'])
def test_a_closed_or_unknown_bill_keeps_the_button_working(hotel_a, arg):
    booking = book(hotel_a, -1, 2, Status.CHECKED_IN)

    html = render_check_out(hotel_a, booking, f'booking today "folio" {arg}')

    assert forms_to(html, action_url('check_out', booking)) == 1
    assert BILL_NOTE not in html
    assert 'disabled' not in html.replace('x-bind:disabled', '')


def test_the_check_in_day_is_the_reason_shown_even_when_the_bill_is_open(hotel_a):
    booking = book(hotel_a, 0, 2, Status.CHECKED_IN)

    html = render_check_out(hotel_a, booking, 'booking today "folio" bill_closed=False')

    assert TOO_EARLY_NOTE in html
    assert BILL_NOTE not in html
    assert '<form' not in html
    assert re.search(r'<button type="button" class="rr-btn ci-btn" disabled', html)


@pytest.mark.parametrize('start, bill_arg', [(0, 'bill_closed=None'), (-1, 'bill_closed=False')],
                         ids=['too-early', 'open-bill'])
def test_a_disabled_button_describes_itself_with_an_id_that_exists(hotel_a, start, bill_arg):
    booking = book(hotel_a, start, 2, Status.CHECKED_IN)

    html = render_check_out(hotel_a, booking, f'booking today "folio" {bill_arg}')

    described_by = re.search(r'aria-describedby="([^"]+)"', html).group(1)
    assert dom_ids(html).count(described_by) == 1


def test_dom_ids_stay_unique_when_the_folio_section_joins_the_others(hotel_a):
    booking = book(hotel_a, -1, 2, Status.CHECKED_IN)

    html = ''.join(
        render_check_out(hotel_a, booking, f'booking today "{section}" bill_closed=False')
        for section in ('departures', 'inhouse', 'folio')
    )

    ids = dom_ids(html)
    assert len(ids) == 3
    assert len(ids) == len(set(ids))
    assert any(found.startswith('co-folio-') for found in ids)


# ---------- Messages after a POST ----------

def test_a_successful_check_in_shows_a_status_message_after_the_redirect(client, hotel_a, login_member):
    booking = book(hotel_a, 0, 2)
    login_member(hotel_a)

    response = client.post(action_url('check_in', booking), ID, follow=True)

    page = response.content.decode()
    assert response.redirect_chain[-1][0] == today_url(hotel_a)
    assert f'เช็คอินการจอง {booking.code} เรียบร้อยแล้ว' in page
    assert 'class="rr-alert rr-alert--success" role="status"' in page
    # The message is shown once
    assert 'rr-alert' not in get_page(client, hotel_a)


def test_a_rejected_check_in_shows_an_alert(client, hotel_a, login_member):
    booking = book(hotel_a, 0, 2)
    login_member(hotel_a)

    response = client.post(action_url('check_in', booking), {**ID, 'id_number': ''}, follow=True)

    page = response.content.decode()
    assert 'class="rr-alert rr-alert--error" role="alert"' in page
    booking.refresh_from_db()
    assert booking.status == Status.CONFIRMED


def test_check_out_with_an_open_bill_shows_the_service_error_on_the_page(client, hotel_a, login_member):
    booking = book(hotel_a, -1, 2, Status.CHECKED_IN)
    login_member(hotel_a)

    response = client.post(action_url('check_out', booking), follow=True)

    page = response.content.decode()
    assert 'class="rr-alert rr-alert--error" role="alert"' in page
    assert 'กรุณารับชำระและปิดบิลก่อนเช็คเอาต์' in page
    booking.refresh_from_db()
    assert booking.status == Status.CHECKED_IN


def test_a_successful_check_out_shows_a_status_message(client, hotel_a, login_member):
    booking = book(hotel_a, -1, 2, Status.CHECKED_IN)
    close_bill(hotel_a, booking.pk, user=None)
    login_member(hotel_a)

    response = client.post(action_url('check_out', booking), follow=True)

    page = response.content.decode()
    assert f'เช็คเอาต์การจอง {booking.code} เรียบร้อยแล้ว' in page
    assert 'class="rr-alert rr-alert--success" role="status"' in page


def test_messages_partial_uses_the_right_role_for_each_level():
    html = render_to_string('partials/messages.html', {'messages': [
        Message(constants.SUCCESS, 'ok'),
        Message(constants.ERROR, 'bad'),
        Message(constants.WARNING, 'careful <b>'),
    ]})

    assert 'rr-alert--success" role="status">ok<' in html
    assert 'rr-alert--error" role="alert">bad<' in html
    assert 'rr-alert--warning" role="status">careful &lt;b&gt;<' in html


def test_messages_partial_renders_nothing_without_messages():
    assert 'rr-alert' not in render_to_string('partials/messages.html', {'messages': []})


# ---------- Privacy, isolation, roles, queries ----------

def test_the_page_shows_no_contact_details(client, hotel_a, login_member):
    arriving = book(hotel_a, 0, 2)
    staying = book(hotel_a, -1, 2, Status.CHECKED_IN)
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    for booking in (arriving, staying):
        assert booking.guest.email not in page
        assert booking.guest.phone not in page


def test_nothing_of_another_hotel_is_in_the_html(client, hotel_a, hotel_b, login_member):
    mine = book(hotel_a, 0, 2)
    others = [book(hotel_b, 0, 2), book(hotel_b, -1, 2, Status.CHECKED_IN)]
    login_member(hotel_a)

    page = get_page(client, hotel_a)

    assert mine.code in page
    for other in others:
        assert str(other.pk) not in page
        assert other.pk.hex not in page
        assert other.code not in page
        assert hotel_b.slug not in page


@pytest.mark.parametrize('role', [Role.OWNER, Role.MANAGER, Role.FRONT_DESK])
def test_front_desk_roles_get_the_controls(client, hotel_a, login_member, role):
    arriving = book(hotel_a, 0, 2)
    staying = book(hotel_a, -1, 2, Status.CHECKED_IN)
    # bill must be closed for the check-out form (bill_closed)
    close_the_bill(staying)
    login_member(hotel_a, role=role)

    page = get_page(client, hotel_a)

    assert forms_to(page, action_url('check_in', arriving)) == 1
    assert forms_to(page, action_url('check_out', staying)) == 1


def test_housekeeping_gets_no_page_and_so_no_controls(client, hotel_a, login_member):
    book(hotel_a, 0, 2)
    login_member(hotel_a, role=Role.HOUSEKEEPING)

    assert client.get(today_url(hotel_a)).status_code == 403


def test_staff_of_another_hotel_cannot_open_the_page(client, hotel_a, hotel_b, login_member):
    book(hotel_a, 0, 2)
    login_member(hotel_b, role=Role.OWNER)

    assert client.get(today_url(hotel_a)).status_code == 404


def test_query_count_does_not_grow_with_the_number_of_bookings(client, hotel_a, login_member):
    def add_bookings(count):
        for _ in range(count):
            book(hotel_a, 0, 2, rooms=({}, {}))                          # ready to check in
            book(hotel_a, 0, 2, rooms=({'hk_status': DIRTY},))           # blocked
            book(hotel_a, -1, 3, Status.CHECKED_IN, rooms=({}, {}))      # check-out, leaving early
            book(hotel_a, -2, 0, Status.CHECKED_IN)                      # departs today, two sections

    def count_queries():
        with CaptureQueriesContext(connection) as queries:
            assert client.get(today_url(hotel_a)).status_code == 200
        return len(queries)

    login_member(hotel_a)
    add_bookings(1)
    count_queries()  # warm up per-process caches
    few = count_queries()

    add_bookings(6)

    assert count_queries() == few
