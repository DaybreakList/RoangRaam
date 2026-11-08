"""
Check-in form and check-out button for the rows of the "Today" lists (TASK-43, TASK-44 UI).
check_out_button also works on the Folio page, which has a booking but no row.

Both tags only read what daily_lists() already loaded (guest, active_rooms with room and
room_type), so they add no query per row. The date comes from the caller (`lists.today`,
the property's own date), never from the server clock.
"""

from django import template
from django.urls import reverse
from django.utils.formats import date_format

from apps.bookings.models import Guest
from apps.frontdesk.check_in_out_forms import CheckInForm
from apps.frontdesk.services.check_in_out import ROOM_OCCUPIED_MESSAGE
from apps.rooms.models import Room

register = template.Library()


def _action_url(request, name, booking):
    return reverse(f'check_in_out:{name}', kwargs={
        'property_slug': request.property.slug,
        'booking_id': booking.pk,
    })


def _room_problems(booking_rooms, occupied_room_numbers):
    """Why the service would refuse these rooms, in the service's own words."""
    if not booking_rooms:
        return ['การจองนี้ไม่มีห้องที่ใช้งานอยู่']
    problems = []
    for booking_room in booking_rooms:
        room = booking_room.room
        if not room.is_active:
            problems.append(f'ห้อง {room.number} ปิดใช้งานอยู่')
        elif room.number in occupied_room_numbers:
            # Comes first: cleaning the room would not help while the last guest is still in it
            problems.append(ROOM_OCCUPIED_MESSAGE.format(number=room.number))
        elif room.hk_status != Room.HousekeepingStatus.CLEAN:
            problems.append(f'ห้อง {room.number} ยังไม่ได้ทำความสะอาด')
    return problems


@register.inclusion_tag('frontdesk/check_in_out/_check_in.html', takes_context=True)
def check_in_form(context, row):
    """The check-in button and its dialog for one CONFIRMED row; blocked while a room is not ready."""
    request = context['request']
    booking = row.booking
    booking_rooms = booking.active_rooms
    dom_id = f'ci-{booking.pk.hex}'

    form = CheckInForm(auto_id=f'{dom_id}-%s', initial={'id_type': Guest.IdType.ID_CARD})
    # Client-side helpers only: the server still validates everything in CheckInForm
    form.fields['id_type'].widget.attrs['x-model'] = 'idType'
    form.fields['id_number'].widget.attrs.update({
        'autocomplete': 'off',
        'x-bind:inputmode': "idType === idCard ? 'numeric' : 'text'",
        # 17, not 13: the desk types the card as 1-2345-67890-12-3 and CheckInForm
        # strips the dashes before counting the digits (E2E case 3-03)
        'x-bind:maxlength': 'idType === idCard ? 17 : 50',
    })
    return {
        'dom_id': dom_id,
        'booking': booking,
        'form': form,
        'id_card_value': Guest.IdType.ID_CARD.value,
        'rooms': [
            {
                'number': booking_room.room.number,
                'type_name': booking_room.room_type.name,
                'status_label': booking_room.room.get_hk_status_display(),
                'is_clean': booking_room.room.hk_status == Room.HousekeepingStatus.CLEAN,
            }
            for booking_room in booking_rooms
        ],
        'problems': _room_problems(booking_rooms, row.occupied_room_numbers),
        'action_url': _action_url(request, 'check_in', booking),
        'room_status_url': reverse('room_status:index', kwargs={'property_slug': request.property.slug}),
        'csrf_token': context.get('csrf_token'),
    }


@register.inclusion_tag('frontdesk/check_in_out/_check_out.html', takes_context=True)
def check_out_button(context, booking, today, section_id, show_folio_link=True, bill_closed=None):
    """
    The check-out button for one CHECKED_IN booking.

    today: the hotel's date, given by the caller (lists.today, property_today(request.property)).
    section_id: makes the DOM ids unique when the button is on the page more than once.
    show_folio_link: False hides the "pay / close bill" link (the Folio page links to itself).
    bill_closed: None = unknown, the button works and the service answers if the bill is open;
        False = the button is disabled until the bill is closed; True = the button works.

    Disabled on the check-in day, like the service (that reason wins over an open bill);
    asks first when leaving before check_out_date.

    In the Today lists:  {% check_out_button row.booking lists.today section_id %}
    On the Folio page:   {% check_out_button booking today "folio" show_folio_link=False bill_closed=True %}
    """
    request = context['request']
    leaves_early = booking.check_out_date > today
    confirm_text = f'ยืนยันเช็คเอาต์การจอง {booking.code} ({booking.guest})?'
    if leaves_early:
        confirm_text += (
            f'\n\nการจองนี้ยังไม่ถึงวันเช็คเอาต์ ({date_format(booking.check_out_date, "j M Y")}) '
            'การออกก่อนกำหนดจะไม่คืนเงินสำหรับคืนที่เหลือ'
        )
    return {
        'dom_id': f'co-{section_id}-{booking.pk.hex}',
        'booking': booking,
        'too_early': today <= booking.check_in_date,
        'blocked_by_bill': bill_closed is False,
        'show_folio_link': show_folio_link,
        'confirm_text': confirm_text,
        'action_url': _action_url(request, 'check_out', booking),
        'folio_url': reverse('folio:detail', kwargs={
            'property_slug': request.property.slug,
            'booking_id': booking.pk,
        }),
        'csrf_token': context.get('csrf_token'),
    }
