from rest_framework import serializers

from apps.rooms.models import RoomType

from .models import Booking, BookingRoom, Guest

MAX_ROOMS_PER_BOOKING = 20

DATE_ERRORS = {
    'required': 'กรุณาระบุวันที่',
    'invalid': 'รูปแบบวันที่ไม่ถูกต้อง (ใช้ YYYY-MM-DD)',
    'null': 'กรุณาระบุวันที่',
}


class AvailabilityQuerySerializer(serializers.Serializer):
    """Validates the query string of the availability API."""

    check_in = serializers.DateField(error_messages=DATE_ERRORS)
    check_out = serializers.DateField(error_messages=DATE_ERRORS)
    guests = serializers.IntegerField(
        required=False,
        min_value=1,
        error_messages={
            'invalid': 'จำนวนผู้เข้าพักต้องเป็นตัวเลข',
            'min_value': 'จำนวนผู้เข้าพักต้องมีอย่างน้อย 1 คน',
            'max_string_length': 'จำนวนผู้เข้าพักยาวเกินไป',
        },
    )


class AvailableRoomTypeSerializer(serializers.ModelSerializer):
    """
    One room type from `get_available_room_types()`.

    Expects the queryset to come through `with_room_type_details()` so
    amenities and images are prefetched.
    """

    amenities = serializers.SerializerMethodField()
    images = serializers.SerializerMethodField()
    available_count = serializers.IntegerField()
    # Serialized as a decimal string (COERCE_DECIMAL_TO_STRING), never float
    total_price = serializers.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        model = RoomType
        fields = [
            'id', 'name', 'description', 'max_occupancy', 'bed_type', 'size_sqm',
            'amenities', 'images', 'available_count', 'total_price',
        ]
        read_only_fields = fields

    def get_amenities(self, room_type):
        return [amenity.name for amenity in room_type.amenities.all()]

    def get_images(self, room_type):
        return [image.image.url for image in room_type.images.all()]


def thai_errors(label):
    """Thai messages for the DRF error keys our input fields can raise."""
    return {
        'required': f'กรุณาระบุ{label}',
        'null': f'กรุณาระบุ{label}',
        'blank': f'กรุณาระบุ{label}',
        'invalid': f'{label}ไม่ถูกต้อง',
        'max_length': f'{label}ยาวเกินไป',
        'max_string_length': f'{label}ยาวเกินไป',
        'invalid_choice': f'{label}ไม่ถูกต้อง',
    }


class GuestInputSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=100, error_messages=thai_errors('ชื่อ'))
    last_name = serializers.CharField(max_length=100, error_messages=thai_errors('นามสกุล'))
    email = serializers.EmailField(error_messages=thai_errors('อีเมล'))
    phone = serializers.CharField(max_length=30, error_messages=thai_errors('เบอร์โทร'))


class RoomRequestSerializer(serializers.Serializer):
    room_type_id = serializers.IntegerField(min_value=1, error_messages={
        **thai_errors('ประเภทห้อง'), 'min_value': 'ประเภทห้องไม่ถูกต้อง',
    })
    quantity = serializers.IntegerField(
        min_value=1, max_value=MAX_ROOMS_PER_BOOKING,
        error_messages={
            **thai_errors('จำนวนห้อง'),
            'min_value': 'จำนวนห้องต้องมีอย่างน้อย 1 ห้อง',
            'max_value': f'จองได้สูงสุด {MAX_ROOMS_PER_BOOKING} ห้องต่อครั้ง',
        },
    )


class BookingInputSerializer(serializers.Serializer):
    check_in = serializers.DateField(error_messages=DATE_ERRORS)
    check_out = serializers.DateField(error_messages=DATE_ERRORS)
    adults = serializers.IntegerField(min_value=1, error_messages={
        **thai_errors('จำนวนผู้ใหญ่'), 'min_value': 'ต้องมีผู้ใหญ่อย่างน้อย 1 คน',
    })
    children = serializers.IntegerField(min_value=0, default=0, error_messages={
        **thai_errors('จำนวนเด็ก'), 'min_value': 'จำนวนเด็กต้องไม่ติดลบ',
    })
    payment_option = serializers.ChoiceField(
        choices=Booking.PaymentOption.choices, error_messages=thai_errors('วิธีชำระเงิน'),
    )
    guest = GuestInputSerializer(error_messages=thai_errors('ข้อมูลผู้เข้าพัก'))
    idempotency_key = serializers.UUIDField(
        required=False,
        error_messages={'invalid': 'idempotency_key ต้องเป็น UUID'},
    )
    rooms = serializers.ListField(
        child=RoomRequestSerializer(),
        min_length=1,
        error_messages={
            **thai_errors('ห้องที่ต้องการจอง'),
            'not_a_list': 'ห้องที่ต้องการจองต้องเป็นรายการ',
            'empty': 'กรุณาเลือกห้องอย่างน้อย 1 ห้อง',
            'min_length': 'กรุณาเลือกห้องอย่างน้อย 1 ห้อง',
        },
    )

    def validate_rooms(self, rooms):
        if sum(item['quantity'] for item in rooms) > MAX_ROOMS_PER_BOOKING:
            raise serializers.ValidationError(f'จองได้สูงสุด {MAX_ROOMS_PER_BOOKING} ห้องต่อครั้ง')
        return rooms


class GuestSerializer(serializers.ModelSerializer):
    """
    Email only. The booking API is public and reuses an existing guest by email,
    so returning the stored name or phone would leak them to whoever typed it.
    """

    class Meta:
        model = Guest
        fields = ['email']
        read_only_fields = fields


class BookingRoomSerializer(serializers.ModelSerializer):
    room_type_id = serializers.IntegerField()
    room_type_name = serializers.CharField(source='room_type.name')
    room_number = serializers.CharField(source='room.number')
    nights = serializers.SerializerMethodField()

    class Meta:
        model = BookingRoom
        fields = ['room_type_id', 'room_type_name', 'room_number', 'subtotal', 'nights']
        read_only_fields = fields

    def get_nights(self, booking_room):
        # Decimal -> string to match COERCE_DECIMAL_TO_STRING
        return [
            {'night': rate.night.isoformat(), 'price': str(rate.price)}
            for rate in booking_room.night_rates.all()
        ]


class BookingSerializer(serializers.ModelSerializer):
    """Expects an instance from `booking_with_details()` (guest and rooms preloaded)."""

    guest = GuestSerializer()
    rooms = BookingRoomSerializer(source='booking_rooms', many=True)
    nights = serializers.IntegerField()

    class Meta:
        model = Booking
        fields = [
            'id', 'code', 'status', 'payment_option', 'check_in_date', 'check_out_date',
            'nights', 'adults', 'children', 'total_amount', 'guest', 'rooms', 'created_at',
        ]
        read_only_fields = fields
