from collections import Counter, defaultdict
from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import (
    Count,
    DecimalField,
    F,
    IntegerField,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
)
from django.http import Http404
from django.utils import timezone
from psycopg.types.range import DateRange

from apps.rooms.models import Amenity, DailyRate, Room, RoomType, RoomTypeImage

from .models import Booking, BookingNightRate, BookingRoom, Guest

ROOM_TAKEN_MESSAGE = 'ห้องที่เลือกเพิ่งถูกจองไป กรุณาค้นหาห้องว่างอีกครั้ง'

INITIAL_STATUS = {
    Booking.PaymentOption.FULL: Booking.Status.PENDING_PAYMENT,
    Booking.PaymentOption.PAY_AT_HOTEL: Booking.Status.CONFIRMED,
}


def validate_stay_dates(check_in, check_out):
    errors = {}
    if check_in < timezone.localdate():
        errors['check_in'] = 'วันเช็คอินต้องไม่ใช่วันที่ผ่านมาแล้ว'
    if check_out <= check_in:
        errors['check_out'] = 'วันเช็คเอาต์ต้องอยู่หลังวันเช็คอิน'
    if errors:
        raise ValidationError(errors)


def booked_room_ids(property, check_in, check_out):
    """Rooms held by an active booking that overlaps [check_in, check_out)."""
    return BookingRoom.scoped.for_property(property).filter(
        is_active=True,
        stay_range__overlap=DateRange(check_in, check_out),
    ).values('room_id')


def get_available_room_types(property, check_in, check_out, guests=None):
    """
    Room types of `property` that still have a free room for every night
    of [check_in, check_out).

    Each result is annotated with:
      - available_count: active rooms of this type with no overlapping booking
      - total_price: sum of each night's DailyRate (per-night, never rate x nights)

    Room types missing a DailyRate for any night are excluded because they
    cannot be priced. Runs as a single query.

    `guests` keeps only types whose free rooms together can host the whole
    group (max_occupancy x available_count), since a booking may hold
    several rooms.
    """
    validate_stay_dates(check_in, check_out)
    if guests is not None and guests < 1:
        raise ValidationError({'guests': 'จำนวนผู้เข้าพักต้องมีอย่างน้อย 1 คน'})
    nights = (check_out - check_in).days

    rates = (
        DailyRate.scoped.for_property(property)
        .filter(room_type=OuterRef('pk'), date__gte=check_in, date__lt=check_out)
        .order_by()
        .values('room_type')
    )

    room_types = (
        RoomType.scoped.for_property(property)
        .annotate(
            available_count=Count(
                'rooms',
                filter=Q(rooms__is_active=True)
                & ~Q(rooms__id__in=booked_room_ids(property, check_in, check_out)),
                distinct=True,
            ),
            priced_nights=Subquery(
                rates.annotate(n=Count('pk')).values('n'), output_field=IntegerField(),
            ),
            total_price=Subquery(
                rates.annotate(total=Sum('price')).values('total'),
                output_field=DecimalField(max_digits=12, decimal_places=2),
            ),
        )
        .filter(available_count__gt=0, priced_nights=nights)
        .order_by('name', 'pk')
    )
    if guests is not None:
        room_types = room_types.annotate(
            capacity=F('max_occupancy') * F('available_count'),
        ).filter(capacity__gte=guests)
    return room_types


def with_room_type_details(room_types, property):
    """Prefetch amenities and images (scoped to `property`) to avoid N+1 when rendering."""
    return room_types.prefetch_related(
        Prefetch('amenities', queryset=Amenity.scoped.for_property(property)),
        Prefetch('images', queryset=RoomTypeImage.scoped.for_property(property).order_by('pk')),
    )


def create_booking(property, **kwargs):
    """Same as get_or_create_booking() but returns only the booking."""
    booking, _created = get_or_create_booking(property, **kwargs)
    return booking


def get_or_create_booking(
    property, *, check_in, check_out, rooms, guest, adults, children, payment_option,
    idempotency_key=None,
):
    """
    Book several rooms on one bill and assign real rooms immediately.

    Returns (booking, created). `created` is False only when `idempotency_key`
    was already used, so callers can do once-only work such as sending the
    confirmation email. When it returns, the booking is already committed.

    `rooms` is a list of {'room_type_id': int, 'quantity': int}; repeated types are merged.
    `guest` is a dict with first_name, last_name, email and phone. An existing guest
    with the same email (case-insensitive) in this property is reused as is.

    Concurrency: requested RoomType rows are locked with select_for_update() in pk
    order, so two bookings for the same type run one after another and cannot
    deadlock. The exclusion constraint on BookingRoom still guards any writer
    that skips these locks; its IntegrityError becomes a Thai ValidationError.

    Idempotency: when `idempotency_key` (a UUID) was already used in this property,
    (that booking, False) is returned and nothing new is created, even if the
    payload differs. The key is re-checked after the locks are taken, so a
    duplicate that waited behind the original gets the original booking rather
    than "rooms not enough"; a duplicate that skipped those locks (other room
    types) hits the unique constraint and is answered the same way.

    Query count is constant no matter how many rooms are booked.
    """
    if idempotency_key is not None:
        existing = _booking_for_key(property, idempotency_key)
        if existing is not None:
            return existing, False
    validate_stay_dates(check_in, check_out)
    if payment_option not in INITIAL_STATUS:
        raise ValidationError({'payment_option': 'วิธีชำระเงินไม่ถูกต้อง'})
    quantities = _merge_room_requests(rooms)
    nights = [check_in + timedelta(days=i) for i in range((check_out - check_in).days)]

    try:
        with transaction.atomic():
            room_types = _lock_room_types(property, quantities)
            if idempotency_key is not None:
                existing = _booking_for_key(property, idempotency_key)
                if existing is not None:
                    return existing, False
            _check_occupancy(room_types, quantities, adults, children)
            assigned = _assign_rooms(property, room_types, quantities, check_in, check_out)
            prices = _nightly_prices(property, room_types, nights)

            guest_obj = _get_or_create_guest(property, guest)

            subtotals = {
                room_type.pk: sum((prices[room_type.pk, night] for night in nights), Decimal('0.00'))
                for room_type in room_types
            }
            booking = Booking(
                property=property,
                guest=guest_obj,
                status=INITIAL_STATUS[payment_option],
                payment_option=payment_option,
                check_in_date=check_in,
                check_out_date=check_out,
                adults=adults,
                children=children,
                total_amount=sum(
                    (subtotals[room.room_type_id] for room in assigned), Decimal('0.00'),
                ),
                idempotency_key=idempotency_key,
            )
            # A racing duplicate key is settled by the DB constraint below
            booking.full_clean(exclude=['idempotency_key'])
            booking.save()

            booking_rooms = BookingRoom.scoped.bulk_create([
                BookingRoom(
                    property=property,
                    booking=booking,
                    room_type_id=room.room_type_id,
                    room=room,
                    stay_range=DateRange(check_in, check_out),
                    subtotal=subtotals[room.room_type_id],
                )
                for room in assigned
            ])
            BookingNightRate.scoped.bulk_create([
                BookingNightRate(
                    property=property,
                    booking_room=booking_room,
                    night=night,
                    price=prices[booking_room.room_type_id, night],
                )
                for booking_room in booking_rooms
                for night in nights
            ])
    except IntegrityError as error:
        if idempotency_key is not None:
            existing = _booking_for_key(property, idempotency_key)
            if existing is not None:
                return existing, False
        # Last line of defence: the exclusion constraint saw an overlap
        raise ValidationError({'rooms': ROOM_TAKEN_MESSAGE}) from error
    return booking, True


def _booking_for_key(property, idempotency_key):
    return Booking.scoped.for_property(property).filter(idempotency_key=idempotency_key).first()


def find_booking(property, code, email):
    """
    A guest looks up their booking with the 8-character code and their email.

    Code and email are matched ignoring case and surrounding spaces. A wrong
    code and a wrong email both raise Http404, so the answer never reveals
    whether a code exists. Returns the booking from `booking_with_details()`.
    """
    code = (code or '').strip().upper()
    email = (email or '').strip()
    if not code or not email:
        raise Http404
    booking_id = (
        Booking.scoped.for_property(property)
        .filter(code=code, guest__email__iexact=email)
        .values_list('pk', flat=True)
        .first()
    )
    if booking_id is None:
        raise Http404
    return booking_with_details(property, booking_id)


def _merge_room_requests(rooms):
    quantities = Counter()
    for item in rooms:
        quantities[item['room_type_id']] += item['quantity']
    if not quantities or any(qty < 1 for qty in quantities.values()):
        raise ValidationError({'rooms': 'กรุณาเลือกห้องอย่างน้อย 1 ห้อง'})
    return quantities


def _lock_room_types(property, quantities):
    """Lock requested types in pk order. Unknown or other-property ids are 404."""
    room_types = list(
        RoomType.scoped.for_property(property)
        .filter(pk__in=quantities)
        .select_for_update()
        .order_by('pk')
    )
    if len(room_types) != len(quantities):
        raise Http404
    return room_types


def _check_occupancy(room_types, quantities, adults, children):
    capacity = sum(room_type.max_occupancy * quantities[room_type.pk] for room_type in room_types)
    if adults + children > capacity:
        raise ValidationError({
            'adults': f'จำนวนผู้เข้าพักเกินความจุของห้องที่เลือก (พักได้สูงสุด {capacity} คน)',
        })


def _assign_rooms(property, room_types, quantities, check_in, check_out):
    """Pick the first free active rooms of each type, in room-number order."""
    free_rooms = (
        Room.scoped.for_property(property)
        .filter(room_type__in=room_types, is_active=True)
        .exclude(pk__in=booked_room_ids(property, check_in, check_out))
        .order_by('room_type_id', 'number', 'pk')
    )
    free_by_type = defaultdict(list)
    for room in free_rooms:
        free_by_type[room.room_type_id].append(room)

    assigned = []
    for room_type in room_types:
        wanted = quantities[room_type.pk]
        free = free_by_type[room_type.pk]
        if len(free) < wanted:
            raise ValidationError({
                'rooms': f'ห้องประเภท {room_type.name} เหลือไม่พอ (ว่าง {len(free)} ห้อง)',
            })
        assigned.extend(free[:wanted])
    return assigned


def _nightly_prices(property, room_types, nights):
    """{(room_type_id, night): price}; every night of every type must be priced."""
    prices = {
        (rate.room_type_id, rate.date): rate.price
        for rate in DailyRate.scoped.for_property(property).filter(
            room_type__in=room_types, date__gte=nights[0], date__lte=nights[-1],
        )
    }
    for room_type in room_types:
        if any((room_type.pk, night) not in prices for night in nights):
            raise ValidationError({
                'rooms': f'ห้องประเภท {room_type.name} ยังไม่เปิดราคาครบทุกคืนที่เลือก',
            })
    return prices


def _guest_fields(guest):
    fields = {key: guest.get(key, '') for key in ('first_name', 'last_name', 'email', 'phone')}
    fields['email'] = fields['email'].strip()
    return fields


def _get_or_create_guest(property, data):
    """
    Return this property's guest for the email, creating it if needed.

    An existing profile is never overwritten: the booking API is public, so
    anyone can type someone else's email. Two bookings racing with a new email
    both end up on the same row: the loser's insert fails inside a savepoint
    and it reads the winner's guest instead.
    """
    candidate = Guest(property=property, **_guest_fields(data))
    # Validate the input itself; uniqueness is settled by the DB below
    candidate.full_clean(validate_constraints=False)

    guests = Guest.scoped.for_property(property)
    existing = guests.filter(email__iexact=candidate.email).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            candidate.save()
    except IntegrityError:
        return guests.get(email__iexact=candidate.email)
    return candidate


def booking_with_details(property, booking_id):
    """One booking with guest, rooms and nightly prices loaded in a fixed number of queries."""
    return (
        Booking.scoped.for_property(property)
        .select_related('guest')
        .prefetch_related(
            Prefetch(
                'booking_rooms',
                queryset=BookingRoom.scoped.for_property(property)
                .select_related('room', 'room_type')
                .prefetch_related(
                    Prefetch(
                        'night_rates',
                        queryset=BookingNightRate.scoped.for_property(property).order_by('night'),
                    ),
                )
                .order_by('room__number', 'pk'),
            ),
        )
        .get(pk=booking_id)
    )
