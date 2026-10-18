from rest_framework import serializers

from apps.rooms.models import RoomType

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
