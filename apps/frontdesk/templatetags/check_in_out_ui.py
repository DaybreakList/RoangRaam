"""
Check-in form and check-out button for the rows of the "Today" lists (TASK-43, TASK-44 UI).

Both tags only read what daily_lists() already loaded (guest, active_rooms with room and
room_type), so they add no query per row. The date comes from the caller (`lists.today`,
the property's own date), never from the server clock.
"""

from django import template
from django.urls import reverse
from django.utils.formats import date_format

from apps.bookings.models import Guest
from apps.frontdesk.check_in_out_forms import CheckInForm
from apps.rooms.models import Room

register = template.Library()


def _action_url(request, name, booking):
    return reverse(f'check_in_out:{name}', kwargs={
        'property_slug': request.property.slug,
        'booking_id': booking.pk,
    })


def _room_problems(booking_rooms):
    """Why the service would refuse these rooms, in the service's own words."""
    if not booking_rooms:
        return ['การจองนี้ไม่มีห้องที่ใช้งานอยู่']
    problems = []
    for booking_room in booking_rooms:
        room = booking_room.room
        if not room.is_active:
            problems.append(f'ห้อง {room.number} ปิดใช้งานอยู่')
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
        'x-bind:maxlength': 'idType === idCard ? 13 : 50',
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
        'problems': _room_problems(booking_rooms),
        'action_url': _action_url(request, 'check_in', booking),
        'room_status_url': reverse('room_status:index', kwargs={'property_slug': request.property.slug}),
        'csrf_token': context.get('csrf_token'),
    }


@register.inclusion_tag('frontdesk/check_in_out/_check_out.html', takes_context=True)
def check_out_button(context, row, today, section_id):
    """
    The check-out button for one CHECKED_IN row. `today` is lists.today (the hotel's date).
    Disabled on the check-in day, like the service; asks first when leaving before check_out_date.
    """
    request = context['request']
    booking = row.booking
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
        'confirm_text': confirm_text,
        'action_url': _action_url(request, 'check_out', booking),
        'folio_url': reverse('folio:detail', kwargs={
            'property_slug': request.property.slug,
            'booking_id': booking.pk,
        }),
        'csrf_token': context.get('csrf_token'),
    }
