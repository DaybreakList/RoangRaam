from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.http import Http404

from apps.bookings.folio import close_bill
from apps.bookings.models import Booking
from apps.bookings.tests.factories import BookingFactory, BookingRoomFactory
from apps.frontdesk.services.check_in_out import check_in, check_out
from apps.properties.tests.factories import PropertyFactory, UserFactory
from apps.rooms.models import Room
from apps.rooms.tests.factories import RoomFactory, RoomTypeFactory

pytestmark = pytest.mark.django_db

TODAY = date(2026, 11, 2)
CLEAN = Room.HousekeepingStatus.CLEAN
DIRTY = Room.HousekeepingStatus.DIRTY
ID = {'id_type': 'ID_CARD', 'id_number': '1234567890123'}


@pytest.fixture
def hotel():
    return PropertyFactory()


@pytest.fixture
def room_type(hotel):
    return RoomTypeFactory(property=hotel)


@pytest.fixture
def clerk():
    return UserFactory()


def make_room(hotel, room_type, **kwargs):
    return RoomFactory(property=hotel, room_type=room_type, hk_status=kwargs.pop('hk_status', CLEAN), **kwargs)


def make_booking(hotel, rooms, status=Booking.Status.CONFIRMED, check_in_date=TODAY, nights=2):
    booking = BookingFactory(
        property=hotel, status=status,
        check_in_date=check_in_date, check_out_date=check_in_date + timedelta(days=nights),
    )
    for room in rooms:
        BookingRoomFactory(booking=booking, room=room)
    return booking


def do_check_in(hotel, booking, clerk, **kwargs):
    return check_in(hotel, booking.pk, user=clerk, today=kwargs.pop('today', TODAY), **{**ID, **kwargs})


def refresh(*objects):
    for obj in objects:
        obj.refresh_from_db()


# ---------- Check-in ----------

def test_check_in_records_the_guest_id_time_and_staff(hotel, room_type, clerk):
    room_1, room_2 = make_room(hotel, room_type), make_room(hotel, room_type)
    booking = make_booking(hotel, [room_1, room_2])

    do_check_in(hotel, booking, clerk, nationality=' ไทย ')

    refresh(booking, booking.guest)
    assert booking.status == Booking.Status.CHECKED_IN
    assert (booking.guest.id_type, booking.guest.id_number, booking.guest.nationality) == (
        'ID_CARD', '1234567890123', 'ไทย',
    )
    for booking_room in booking.booking_rooms.all():
        assert booking_room.checked_in_at is not None
        assert booking_room.checked_in_by == clerk


def test_check_in_can_change_to_a_free_clean_room_of_the_same_type(hotel, room_type, clerk):
    booked, other = make_room(hotel, room_type), make_room(hotel, room_type)
    booking = make_booking(hotel, [booked])
    booking_room = booking.booking_rooms.get()

    do_check_in(hotel, booking, clerk, room_changes={booking_room.pk: other.pk})

    refresh(booking_room)
    assert booking_room.room == other


@pytest.mark.parametrize('status', [
    Booking.Status.PENDING_PAYMENT, Booking.Status.CHECKED_IN,
    Booking.Status.CHECKED_OUT, Booking.Status.CANCELLED,
])
def test_only_confirmed_bookings_can_check_in(hotel, room_type, clerk, status):
    booking = make_booking(hotel, [make_room(hotel, room_type)], status=status)

    with pytest.raises(ValidationError) as error:
        do_check_in(hotel, booking, clerk)

    assert 'status' in error.value.message_dict


@pytest.mark.parametrize('today', [TODAY - timedelta(days=1), TODAY + timedelta(days=1)])
def test_check_in_only_on_the_check_in_date(hotel, room_type, clerk, today):
    booking = make_booking(hotel, [make_room(hotel, room_type)])

    with pytest.raises(ValidationError) as error:
        do_check_in(hotel, booking, clerk, today=today)

    assert 'check_in_date' in error.value.message_dict
    refresh(booking)
    assert booking.status == Booking.Status.CONFIRMED


def test_a_dirty_room_cannot_be_checked_in(hotel, room_type, clerk):
    clean, dirty = make_room(hotel, room_type), make_room(hotel, room_type, hk_status=DIRTY)
    booking = make_booking(hotel, [clean, dirty])

    with pytest.raises(ValidationError) as error:
        do_check_in(hotel, booking, clerk)

    assert error.value.message_dict['rooms'] == [f'ห้อง {dirty.number} ยังไม่ได้ทำความสะอาด']
    refresh(booking, booking.guest)
    assert booking.status == Booking.Status.CONFIRMED
    assert booking.guest.id_number == ''
    assert not booking.booking_rooms.filter(checked_in_at__isnull=False).exists()


def test_missing_id_is_rejected(hotel, room_type, clerk):
    booking = make_booking(hotel, [make_room(hotel, room_type)])

    with pytest.raises(ValidationError) as error:
        do_check_in(hotel, booking, clerk, id_number='   ')

    assert 'id_number' in error.value.message_dict


@pytest.mark.parametrize('change', ['other_type', 'dirty', 'inactive'])
def test_a_changed_room_must_be_the_same_type_clean_and_active(hotel, room_type, clerk, change):
    booking = make_booking(hotel, [make_room(hotel, room_type)])
    booking_room = booking.booking_rooms.get()
    target = {
        'other_type': lambda: make_room(hotel, RoomTypeFactory(property=hotel)),
        'dirty': lambda: make_room(hotel, room_type, hk_status=DIRTY),
        'inactive': lambda: make_room(hotel, room_type, is_active=False),
    }[change]()

    with pytest.raises(ValidationError) as error:
        do_check_in(hotel, booking, clerk, room_changes={booking_room.pk: target.pk})

    assert 'rooms' in error.value.message_dict
    refresh(booking_room)
    assert booking_room.room_id != target.pk


def test_a_changed_room_must_be_free_for_the_whole_stay(hotel, room_type, clerk):
    booking = make_booking(hotel, [make_room(hotel, room_type)])
    busy = make_room(hotel, room_type)
    # Someone else holds the second night only
    make_booking(hotel, [busy], check_in_date=TODAY + timedelta(days=1), nights=1)
    booking_room = booking.booking_rooms.get()

    with pytest.raises(ValidationError) as error:
        do_check_in(hotel, booking, clerk, room_changes={booking_room.pk: busy.pk})

    assert 'rooms' in error.value.message_dict
    refresh(booking_room, booking)
    assert booking_room.room_id != busy.pk
    assert booking.status == Booking.Status.CONFIRMED


def test_two_rooms_of_a_booking_cannot_change_to_the_same_room(hotel, room_type, clerk):
    booking = make_booking(hotel, [make_room(hotel, room_type), make_room(hotel, room_type)])
    target = make_room(hotel, room_type)
    first, second = booking.booking_rooms.order_by('pk')

    with pytest.raises(ValidationError):
        do_check_in(hotel, booking, clerk, room_changes={first.pk: target.pk, second.pk: target.pk})


def test_ids_of_another_hotel_are_404_and_change_nothing(hotel, room_type, clerk):
    other_hotel = PropertyFactory()
    other_room = RoomFactory(property=other_hotel, hk_status=CLEAN)
    other_booking = make_booking(other_hotel, [other_room])
    booking = make_booking(hotel, [make_room(hotel, room_type)])
    booking_room = booking.booking_rooms.get()

    with pytest.raises(Http404):
        do_check_in(hotel, other_booking, clerk)
    with pytest.raises(Http404):
        do_check_in(hotel, booking, clerk, room_changes={booking_room.pk: other_room.pk})
    with pytest.raises(Http404):
        do_check_in(hotel, booking, clerk, room_changes={other_booking.booking_rooms.get().pk: booking_room.room_id})

    refresh(booking, other_booking, booking_room)
    assert booking.status == other_booking.status == Booking.Status.CONFIRMED
    assert booking_room.checked_in_at is None


def test_cancelled_rooms_of_the_booking_are_not_checked_in(hotel, room_type, clerk):
    booking = make_booking(hotel, [make_room(hotel, room_type)])
    released = BookingRoomFactory(booking=booking, room=make_room(hotel, room_type, hk_status=DIRTY), is_active=False)

    do_check_in(hotel, booking, clerk)

    refresh(released)
    assert released.checked_in_at is None


# ---------- Check-out ----------

def checked_in_booking(hotel, room_type, clerk, rooms=1, bill_closed=True):
    """Checked in; the bill is closed unless asked, since check_out() requires it."""
    booking = make_booking(hotel, [make_room(hotel, room_type) for _ in range(rooms)])
    do_check_in(hotel, booking, clerk)
    if bill_closed:
        # Factory bookings cost 0.00, so there is nothing to pay before closing
        close_bill(hotel, booking.pk, user=clerk)
    return booking


def test_check_out_closes_the_stay_and_marks_every_room_dirty(hotel, room_type, clerk):
    booking = checked_in_booking(hotel, room_type, clerk, rooms=2)

    check_out(hotel, booking.pk, today=TODAY + timedelta(days=2))

    refresh(booking)
    assert booking.status == Booking.Status.CHECKED_OUT
    for booking_room in booking.booking_rooms.select_related('room'):
        assert booking_room.checked_out_at is not None
        assert booking_room.room.hk_status == DIRTY


def test_leaving_early_is_allowed_and_keeps_the_price(hotel, room_type, clerk):
    booking = checked_in_booking(hotel, room_type, clerk)
    total = booking.total_amount

    check_out(hotel, booking.pk, today=TODAY + timedelta(days=1))

    refresh(booking)
    assert booking.status == Booking.Status.CHECKED_OUT
    assert booking.total_amount == total


def test_check_out_is_not_allowed_on_the_check_in_date(hotel, room_type, clerk):
    booking = checked_in_booking(hotel, room_type, clerk)

    with pytest.raises(ValidationError) as error:
        check_out(hotel, booking.pk, today=TODAY)

    assert 'check_out_date' in error.value.message_dict
    refresh(booking)
    assert booking.status == Booking.Status.CHECKED_IN


def test_check_out_with_an_open_bill_is_refused_and_changes_nothing(hotel, room_type, clerk):
    booking = checked_in_booking(hotel, room_type, clerk, bill_closed=False)

    with pytest.raises(ValidationError) as error:
        check_out(hotel, booking.pk, today=TODAY + timedelta(days=1))

    assert error.value.message_dict['balance'] == ['กรุณารับชำระและปิดบิลก่อนเช็คเอาต์']
    refresh(booking)
    assert booking.status == Booking.Status.CHECKED_IN
    for booking_room in booking.booking_rooms.select_related('room'):
        assert booking_room.checked_out_at is None
        assert booking_room.room.hk_status == CLEAN


def test_another_bookings_closed_bill_does_not_count(hotel, room_type, clerk):
    booking = checked_in_booking(hotel, room_type, clerk, bill_closed=False)
    checked_in_booking(hotel, room_type, clerk)  # same hotel, bill closed

    with pytest.raises(ValidationError) as error:
        check_out(hotel, booking.pk, today=TODAY + timedelta(days=1))

    assert 'balance' in error.value.message_dict


def test_check_out_works_once_the_bill_is_closed(hotel, room_type, clerk):
    booking = checked_in_booking(hotel, room_type, clerk, bill_closed=False)
    close_bill(hotel, booking.pk, user=clerk)

    check_out(hotel, booking.pk, today=TODAY + timedelta(days=1))

    refresh(booking)
    assert booking.status == Booking.Status.CHECKED_OUT


@pytest.mark.parametrize('status', [Booking.Status.CONFIRMED, Booking.Status.CHECKED_OUT, Booking.Status.CANCELLED])
def test_only_checked_in_bookings_can_check_out(hotel, room_type, status):
    room = make_room(hotel, room_type)
    booking = make_booking(hotel, [room], status=status)

    with pytest.raises(ValidationError) as error:
        check_out(hotel, booking.pk, today=TODAY + timedelta(days=1))

    assert 'status' in error.value.message_dict
    refresh(room)
    assert room.hk_status == CLEAN


def test_check_out_of_another_hotels_booking_is_404(hotel, room_type, clerk):
    other_hotel = PropertyFactory()
    other_booking = checked_in_booking(other_hotel, RoomTypeFactory(property=other_hotel), clerk)

    with pytest.raises(Http404):
        check_out(hotel, other_booking.pk, today=TODAY + timedelta(days=1))

    refresh(other_booking)
    assert other_booking.status == Booking.Status.CHECKED_IN
