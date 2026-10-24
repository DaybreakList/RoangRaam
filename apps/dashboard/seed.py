"""
Demo data for development, staging and presentations.

Run it with `python manage.py seed_demo` (apps/dashboard/management/commands/seed_demo.py).

Two hotels are created with rooms, rates, staff, guests and bookings covering the
last HISTORY_DAYS days and the next FUTURE_DAYS days. Everything is derived from
today's date and a fixed random seed, so repeated runs produce the same shape.

Stays that start before today are written directly because create_booking() rejects
past check-in dates. Stays that start today or later go through create_booking() and
cancel_booking(), the same code the real booking flow uses.

Prices are VAT-inclusive, like the booking API contract (docs/project/API_CONTRACT.md).

Money: past FULL bookings carry the Payment rows a real payment would leave (see
_save_payments). Future bookings are all pay-at-hotel, so they hold no money and
cancelling them never reaches Stripe.
"""

import random
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from psycopg.types.range import DateRange

from apps.bookings.cancellation import cancel_booking
from apps.bookings.models import Booking, BookingNightRate, BookingRoom, Guest, Payment
from apps.bookings.services import create_booking
from apps.properties.models import Membership, Property
from apps.rooms.models import Amenity, DailyRate, Room, RoomType, RoomTypeAmenity

User = get_user_model()
Role = Membership.Role

SEED = 2026
HISTORY_DAYS = 60
RATES_PAST_DAYS = 65
RATES_FUTURE_DAYS = 150
FUTURE_DAYS = 30
FUTURE_ATTEMPTS_PER_ROOM = 4
GUEST_POOL_SIZE = 60
CANCEL_RATE = 0.08
# A seeded guest pays this long after booking
CHARGE_DELAY = timedelta(minutes=5)
STAY_NIGHTS = (1, 1, 2, 2, 3, 4)
GAP_DAYS = (0, 1)
ADULT_CHOICES = (1, 2, 2, 3)
TYPE_WEIGHTS = (5, 3, 1)
WEEKEND_FACTOR = Decimal('1.20')
ARRIVAL_TODAY_NIGHTS = (2, 3)
# (room number, check-in offset, check-out offset) in days from today. Guarantees every
# hotel has departures and in-house guests today, whatever the random stays look like.
TODAY_SCENARIO = (
    ('101', -2, 0),
    ('201', -3, 0),
    ('102', -1, 2),
    ('202', -2, 1),
    ('301', -1, 3),
)

OWNER_EMAIL = 'owner@example.com'
STAFF = (
    ('manager', Role.MANAGER, 'ผู้จัดการ'),
    ('frontdesk', Role.FRONT_DESK, 'พนักงานต้อนรับ'),
    ('housekeeping', Role.HOUSEKEEPING, 'แม่บ้าน'),
)

AMENITIES = ('Wi-Fi', 'เครื่องปรับอากาศ', 'โทรทัศน์', 'ตู้เย็น', 'ที่จอดรถ')

FIRST_NAMES = (
    'สมชาย', 'สมหญิง', 'ประเสริฐ', 'วิชัย', 'สุดา', 'นภา', 'กิตติ', 'มาลี',
    'ธนากร', 'พิมพ์ใจ', 'อนุชา', 'รัตนา', 'ชัยวัฒน์', 'ปวีณา', 'สุรชัย',
)
LAST_NAMES = (
    'ใจดี', 'รักษา', 'แสงทอง', 'พงษ์ไทย', 'มั่นคง', 'ศรีสุข', 'วงศ์ใหญ่',
    'เจริญผล', 'บุญมา', 'ทองดี',
)

# Occupancy is the rough share of room-nights booked, from the start of the history
# to today. Riverside grows over time (a visible chart trend); Hilltop stays flat.
HOTELS = (
    {
        'key': 'riverside',
        'slug': 'demo-riverside',
        'name': 'โรงแรมริเวอร์ไซด์ (เดโม)',
        'address': '99 ถนนริมน้ำ เขตบางรัก กรุงเทพมหานคร (ข้อมูลสมมติ)',
        'occupancy': (0.50, 0.80),
        'room_types': (
            {
                'name': 'Standard', 'max_occupancy': 2, 'bed_type': 'Double', 'size_sqm': 24,
                'price': Decimal('1500.00'), 'description': 'ห้องมาตรฐาน เตียงคู่',
                'amenities': AMENITIES[:3], 'rooms': ('101', '102', '103', '104', '105', '106'),
            },
            {
                'name': 'Deluxe', 'max_occupancy': 3, 'bed_type': 'King', 'size_sqm': 32,
                'price': Decimal('2200.00'), 'description': 'ห้องกว้างขึ้น เตียงคิงไซซ์',
                'amenities': AMENITIES[:4], 'rooms': ('201', '202', '203', '204'),
            },
            {
                'name': 'Suite', 'max_occupancy': 4, 'bed_type': 'King', 'size_sqm': 48,
                'price': Decimal('3800.00'), 'description': 'ห้องสวีท พร้อมห้องนั่งเล่น',
                'amenities': AMENITIES, 'rooms': ('301', '302'),
            },
        ),
    },
    {
        'key': 'hilltop',
        'slug': 'demo-hilltop',
        'name': 'โรงแรมฮิลล์ท็อป (เดโม)',
        'address': '12 ถนนเนินเขา อำเภอเมือง เชียงใหม่ (ข้อมูลสมมติ)',
        'occupancy': (0.45, 0.45),
        'room_types': (
            {
                'name': 'Standard', 'max_occupancy': 2, 'bed_type': 'Double', 'size_sqm': 22,
                'price': Decimal('1200.00'), 'description': 'ห้องมาตรฐาน เตียงคู่',
                'amenities': AMENITIES[:3], 'rooms': ('101', '102', '103', '104', '105'),
            },
            {
                'name': 'Deluxe', 'max_occupancy': 3, 'bed_type': 'King', 'size_sqm': 30,
                'price': Decimal('1800.00'), 'description': 'ห้องวิวเขา เตียงคิงไซซ์',
                'amenities': AMENITIES[:4], 'rooms': ('201', '202', '203'),
            },
            {
                'name': 'Suite', 'max_occupancy': 4, 'bed_type': 'King', 'size_sqm': 44,
                'price': Decimal('3000.00'), 'description': 'ห้องสวีท พร้อมระเบียงส่วนตัว',
                'amenities': AMENITIES, 'rooms': ('301', '302'),
            },
        ),
    },
)

SEED_SLUGS = tuple(hotel['slug'] for hotel in HOTELS)


def _demo_emails():
    emails = {OWNER_EMAIL}
    for hotel in HOTELS:
        emails.update(f'{staff_key}.{hotel["key"]}@example.com' for staff_key, _, _ in STAFF)
    return emails


DEMO_EMAILS = frozenset(_demo_emails())


class DemoDataExists(Exception):
    """The demo hotels are already in the database."""


@dataclass
class Stay:
    room: Room
    check_in: date
    check_out: date
    cancelled: bool = False
    scripted: bool = False


@contextmanager
def _mail_disabled():
    """Seeding must never send email, whatever backend the environment configures."""
    previous = settings.EMAIL_BACKEND
    settings.EMAIL_BACKEND = 'django.core.mail.backends.dummy.EmailBackend'
    try:
        yield
    finally:
        settings.EMAIL_BACKEND = previous


def seed_demo_data(password):
    """Create both demo hotels in one transaction and return a summary per hotel."""
    if Property.objects.filter(slug__in=SEED_SLUGS).exists():
        raise DemoDataExists
    today = timezone.localdate()
    with _mail_disabled(), transaction.atomic():
        owner = _upsert_user(OWNER_EMAIL, password, 'เดโม', 'เจ้าของ')
        return [
            _seed_hotel(spec, index, owner, password, today)
            for index, spec in enumerate(HOTELS)
        ]


def reset_demo_data():
    """
    Delete the demo hotels and everything in them, and demo users who belong to no
    other hotel. Returns the number of hotels removed. Other hotels are not touched.
    """
    with transaction.atomic():
        properties = list(Property.objects.filter(slug__in=SEED_SLUGS))
        for property in properties:
            _delete_hotel(property)
        User.objects.filter(email__in=DEMO_EMAILS, memberships__isnull=True).delete()
    return len(properties)


def _delete_hotel(property):
    # PROTECT foreign keys force this order: children before the rows they point to.
    # Payment.delete() is blocked (append-only) but a queryset delete is not, and
    # the demo hotels are removed whole, so their money rows go with them.
    Payment.scoped.for_property(property).delete()
    BookingNightRate.scoped.for_property(property).delete()
    BookingRoom.scoped.for_property(property).delete()
    Booking.scoped.for_property(property).delete()
    Guest.scoped.for_property(property).delete()
    DailyRate.scoped.for_property(property).delete()
    Room.scoped.for_property(property).delete()
    RoomTypeAmenity.scoped.for_property(property).delete()
    RoomType.scoped.for_property(property).delete()
    Amenity.scoped.for_property(property).delete()
    Membership.objects.filter(property=property).delete()
    property.delete()


def _seed_hotel(spec, index, owner, password, today):
    rng = random.Random(SEED + index)
    property = Property.objects.create(
        name=spec['name'],
        slug=spec['slug'],
        address=spec['address'],
        policy_text='เช็คอินตั้งแต่ 14:00 น. เช็คเอาต์ก่อน 12:00 น. ยกเลิกได้โดยไม่มีค่าใช้จ่าย (ข้อมูลเดโม)',
    )
    front_desk = _create_staff(property, spec['key'], owner, password)
    room_types, rooms = _create_inventory(property, spec)
    prices = _create_rates(property, room_types, spec, today)
    guests = _create_guests(property, spec['key'], rng)

    occupied = defaultdict(list)
    stays = _scripted_stays(rooms, today, occupied)
    stays += _random_history(rooms, spec, today, occupied, rng)
    _save_history(property, stays, guests, prices, front_desk, today, rng)
    _mark_departures_dirty(stays, today)
    _book_future(property, room_types, guests, rng, today, len(rooms))

    return {
        'name': property.name,
        'slug': property.slug,
        'rooms': len(rooms),
        'bookings': Booking.scoped.for_property(property).count(),
    }


def _upsert_user(email, password, first_name, last_name):
    user = User.objects.filter(email=email).first()
    if user is None:
        return User.objects.create_user(
            email=email, password=password, first_name=first_name, last_name=last_name,
        )
    # A demo account that survived a reset keeps working with the current password
    user.set_password(password)
    user.save(update_fields=['password'])
    return user


def _create_staff(property, key, owner, password):
    Membership.objects.create(user=owner, property=property, role=Role.OWNER)
    front_desk = None
    for staff_key, role, label in STAFF:
        user = _upsert_user(f'{staff_key}.{key}@example.com', password, 'เดโม', label)
        Membership.objects.create(user=user, property=property, role=role)
        if role == Role.FRONT_DESK:
            front_desk = user
    return front_desk


def _create_inventory(property, spec):
    amenities = {}
    for name in AMENITIES:
        amenity = Amenity(property=property, name=name)
        amenity.save()
        amenities[name] = amenity

    room_types, rooms = [], []
    for type_spec in spec['room_types']:
        room_type = RoomType(
            property=property,
            name=type_spec['name'],
            max_occupancy=type_spec['max_occupancy'],
            bed_type=type_spec['bed_type'],
            size_sqm=type_spec['size_sqm'],
            description=type_spec['description'],
        )
        room_type.save()
        for name in type_spec['amenities']:
            RoomTypeAmenity(property=property, room_type=room_type, amenity=amenities[name]).save()
        for number in type_spec['rooms']:
            room = Room(
                property=property,
                room_type=room_type,
                number=number,
                floor=int(number[0]),
                hk_status=Room.HousekeepingStatus.CLEAN,
            )
            room.save()
            rooms.append(room)
        room_types.append(room_type)
    return room_types, rooms


def _nightly_price(base, day):
    if day.weekday() in (4, 5):  # Friday and Saturday nights cost more
        return (base * WEEKEND_FACTOR).quantize(Decimal('0.01'))
    return base


def _create_rates(property, room_types, spec, today):
    """Create DailyRate rows and return {(room_type_id, date): price} for pricing bookings."""
    prices, rates = {}, []
    first_day = today - timedelta(days=RATES_PAST_DAYS)
    for room_type, type_spec in zip(room_types, spec['room_types']):
        for offset in range(RATES_PAST_DAYS + RATES_FUTURE_DAYS + 1):
            day = first_day + timedelta(days=offset)
            price = _nightly_price(type_spec['price'], day)
            prices[room_type.pk, day] = price
            rates.append(DailyRate(property=property, room_type=room_type, date=day, price=price))
    DailyRate.scoped.bulk_create(rates)  # validates every row
    return prices


def _create_guests(property, key, rng):
    """A pool of guests with unique emails (one guest profile per email per hotel)."""
    guests = []
    for number in range(1, GUEST_POOL_SIZE + 1):
        guest = Guest(
            property=property,
            first_name=rng.choice(FIRST_NAMES),
            last_name=rng.choice(LAST_NAMES),
            email=f'guest{number:03d}.{key}@example.com',
            phone=f'0800000{number:03d}',
        )
        guest.full_clean()
        guests.append(guest)
    return Guest.scoped.bulk_create(guests)


def _is_free(occupied, room, check_in, check_out):
    return all(check_out <= start or check_in >= end for start, end in occupied[room.pk])


def _scripted_stays(rooms, today, occupied):
    by_number = {room.number: room for room in rooms}
    stays = []
    for number, start, end in TODAY_SCENARIO:
        room = by_number[number]
        stay = Stay(
            room, today + timedelta(days=start), today + timedelta(days=end), scripted=True,
        )
        occupied[room.pk].append((stay.check_in, stay.check_out))
        stays.append(stay)
    return stays


def _start_probability(target):
    """
    Chance of starting a stay on a free day so that a room is booked about `target`
    of the time. A stay of L nights is followed by a gap of G days, so
    target = p * L / (1 + p * (L + G - 1)), solved for p.
    """
    mean_nights = sum(STAY_NIGHTS) / len(STAY_NIGHTS)
    mean_gap = sum(GAP_DAYS) / len(GAP_DAYS)
    return min(0.98, target / (mean_nights - target * (mean_nights + mean_gap - 1)))


def _random_history(rooms, spec, today, occupied, rng):
    """Past and in-house stays, room by room, never overlapping on one room."""
    low, high = spec['occupancy']
    first_day = today - timedelta(days=HISTORY_DAYS)
    stays = []
    for room in rooms:
        day = first_day + timedelta(days=rng.randint(0, 2))
        while day < today:
            progress = (day - first_day).days / HISTORY_DAYS
            if rng.random() >= _start_probability(low + (high - low) * progress):
                day += timedelta(days=1)
                continue
            check_out = day + timedelta(days=rng.choice(STAY_NIGHTS))
            if rng.random() < CANCEL_RATE:
                # A cancelled booking holds no room, so it does not use up the days
                stays.append(Stay(room, day, check_out, cancelled=True))
                day += timedelta(days=1)
            elif _is_free(occupied, room, day, check_out):
                occupied[room.pk].append((day, check_out))
                stays.append(Stay(room, day, check_out))
                day = check_out + timedelta(days=rng.choice(GAP_DAYS))
            else:
                day += timedelta(days=1)
    return stays


def _group_stays(stays, rng):
    """Put some stays with the same dates on one bill, so there are multi-room bookings."""
    by_dates = defaultdict(list)
    for stay in stays:
        by_dates[stay.check_in, stay.check_out, stay.cancelled].append(stay)
    groups = []
    for key in sorted(by_dates):
        pool = by_dates[key]
        rng.shuffle(pool)
        while pool:
            size = 2 if len(pool) >= 2 and rng.random() < 0.3 else 1
            groups.append([pool.pop() for _ in range(size)])
    return groups


def _aware(day, at):
    return timezone.make_aware(datetime.combine(day, at))


def _nights(check_in, check_out):
    return [check_in + timedelta(days=i) for i in range((check_out - check_in).days)]


def _save_history(property, stays, guests, prices, front_desk, today, rng):
    scripted = [[stay] for stay in stays if stay.scripted]
    groups = scripted + _group_stays([stay for stay in stays if not stay.scripted], rng)
    groups.sort(key=lambda group: (group[0].check_in, group[0].room.number))

    bookings, plan, created_ats = [], [], []
    for group in groups:
        first = group[0]
        nights = _nights(first.check_in, first.check_out)
        subtotals = [
            sum((prices[stay.room.room_type_id, night] for night in nights), Decimal('0.00'))
            for stay in group
        ]
        if first.cancelled:
            status = Booking.Status.CANCELLED
            cancelled_at = _aware(first.check_in - timedelta(days=rng.randint(1, 10)), time(10, 0))
            created_at = _aware(first.check_in - timedelta(days=rng.randint(11, 30)), time(12, 0))
        else:
            status = (
                Booking.Status.CHECKED_OUT if first.check_out < today else Booking.Status.CHECKED_IN
            )
            cancelled_at = None
            created_at = _aware(first.check_in - timedelta(days=rng.randint(1, 30)), time(12, 0))
        capacity = sum(stay.room.room_type.max_occupancy for stay in group)
        booking = Booking(
            property=property,
            guest=rng.choice(guests),
            status=status,
            payment_option=rng.choice(
                (Booking.PaymentOption.PAY_AT_HOTEL, Booking.PaymentOption.FULL),
            ),
            check_in_date=first.check_in,
            check_out_date=first.check_out,
            adults=min(rng.choice(ADULT_CHOICES), capacity),
            children=0,
            total_amount=sum(subtotals, Decimal('0.00')),
            cancelled_at=cancelled_at,
        )
        booking.full_clean()
        bookings.append(booking)
        plan.append((booking, group, subtotals))
        created_ats.append(created_at)

    Booking.scoped.bulk_create(bookings)
    # created_at is auto_now_add, so it can only be backdated after the insert
    for booking, created_at in zip(bookings, created_ats):
        booking.created_at = created_at
    Booking.scoped.bulk_update(bookings, ['created_at'])

    booking_rooms, room_stays = [], []
    for booking, group, subtotals in plan:
        stayed = booking.status != Booking.Status.CANCELLED
        checked_out = booking.status == Booking.Status.CHECKED_OUT
        for stay, subtotal in zip(group, subtotals):
            booking_rooms.append(BookingRoom(
                property=property,
                booking=booking,
                room_type=stay.room.room_type,
                room=stay.room,
                stay_range=DateRange(stay.check_in, stay.check_out),
                is_active=not stay.cancelled,
                subtotal=subtotal,
                checked_in_at=_aware(stay.check_in, property.check_in_time) if stayed else None,
                checked_out_at=_aware(stay.check_out, property.check_out_time) if checked_out else None,
                checked_in_by=front_desk if stayed else None,
            ))
            room_stays.append(stay)
    # The exclusion constraint on BookingRoom is the final guard against overlaps
    BookingRoom.scoped.bulk_create(booking_rooms)

    BookingNightRate.scoped.bulk_create([
        BookingNightRate(
            property=property,
            booking_room=booking_room,
            night=night,
            price=prices[booking_room.room_type_id, night],
        )
        for booking_room, stay in zip(booking_rooms, room_stays)
        for night in _nights(stay.check_in, stay.check_out)
    ])
    _save_payments(property, [booking for booking, _, _ in plan])


def _save_payments(property, bookings):
    """
    Money rows for the past FULL bookings, shaped like the ones the Stripe webhook writes.

    A paid stay (checked in or out) has one CHARGE. A cancelled one has its CHARGE and
    the REFUND that cancel_booking() would have recorded, with the same
    `refund-<charge ref>` key, so nothing can refund it a second time. Pay-at-hotel
    bookings have no row: the system does not record cash. References start with
    `seed_`, so they can never be mistaken for a Stripe id.
    """
    payments = []
    for booking in bookings:
        if booking.payment_option != Booking.PaymentOption.FULL:
            continue
        charge_ref = f'seed_pi_{booking.code}'
        payments.append((Payment(
            property=property, booking=booking, kind=Payment.Kind.CHARGE,
            amount=booking.total_amount, provider_ref=charge_ref,
            idempotency_key=f'seed_evt_{booking.code}',
        ), booking.created_at + CHARGE_DELAY))
        if booking.status == Booking.Status.CANCELLED:
            payments.append((Payment(
                property=property, booking=booking, kind=Payment.Kind.REFUND,
                amount=booking.total_amount, provider_ref=f'seed_re_{booking.code}',
                idempotency_key=f'refund-{charge_ref}',
            ), booking.cancelled_at))

    for payment, _ in payments:
        payment.full_clean()
    Payment.scoped.bulk_create([payment for payment, _ in payments])
    # created_at is auto_now_add, so it can only be backdated after the insert
    for payment, created_at in payments:
        payment.created_at = created_at
    Payment.scoped.bulk_update([payment for payment, _ in payments], ['created_at'])


def _mark_departures_dirty(stays, today):
    """Rooms whose guests check out today are waiting to be cleaned; all others are clean."""
    for stay in stays:
        room = stay.room
        if (
            not stay.cancelled
            and stay.check_out == today
            and room.hk_status != Room.HousekeepingStatus.DIRTY
        ):
            room.hk_status = Room.HousekeepingStatus.DIRTY
            room.save()  # Room data is validated on save(); QuerySet.update() is disabled


def _book_future(property, room_types, guests, rng, today, room_count):
    """Bookings that start today or later, made through the real booking service."""
    # (check-in, nights, scripted). Scripted ones are today's arrivals: one Standard room,
    # never cancelled. Every future booking is pay-at-hotel (CONFIRMED at once): a FULL
    # booking would wait in PENDING_PAYMENT for a Stripe payment the demo never makes,
    # and is released after PAYMENT_WINDOW, so its rooms would disappear from search.
    # It also means cancelling any seeded future booking never calls Stripe.
    requests = [(today, nights, True) for nights in ARRIVAL_TODAY_NIGHTS]
    for _ in range(FUTURE_ATTEMPTS_PER_ROOM * room_count):
        check_in = today + timedelta(days=rng.randint(1, FUTURE_DAYS))
        requests.append((check_in, rng.choice(STAY_NIGHTS), False))

    for check_in, nights, scripted in requests:
        room_type = room_types[0] if scripted else rng.choices(room_types, weights=TYPE_WEIGHTS)[0]
        quantity = 1 if scripted or rng.random() >= 0.3 else 2
        guest = rng.choice(guests)
        try:
            booking = create_booking(
                property,
                check_in=check_in,
                check_out=check_in + timedelta(days=nights),
                rooms=[{'room_type_id': room_type.pk, 'quantity': quantity}],
                guest={
                    'first_name': guest.first_name,
                    'last_name': guest.last_name,
                    'email': guest.email,
                    'phone': guest.phone,
                },
                adults=min(rng.choice(ADULT_CHOICES), room_type.max_occupancy * quantity),
                children=0,
                payment_option=Booking.PaymentOption.PAY_AT_HOTEL,
            )
        except ValidationError:
            continue  # sold out for these dates: skip it
        if not scripted and rng.random() < CANCEL_RATE:
            cancel_booking(property, booking.pk)
