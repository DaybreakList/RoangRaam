from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.properties.permissions import PublicPropertyViewMixin

from .serializers import (
    AvailabilityQuerySerializer,
    AvailableRoomTypeSerializer,
    BookingInputSerializer,
    BookingSerializer,
)
from .services import (
    booking_with_details,
    create_booking,
    get_available_room_types,
    with_room_type_details,
)


class AvailabilityAPIView(PublicPropertyViewMixin, APIView):
    """
    GET /api/p/<property_slug>/availability/?check_in=YYYY-MM-DD&check_out=YYYY-MM-DD&guests=N

    Public: guests search without logging in. `request.property` is set by
    PropertyMiddleware, so only this property's room types are returned.
    """

    permission_classes = [AllowAny]

    def get(self, request):
        query = AvailabilityQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        check_in = query.validated_data['check_in']
        check_out = query.validated_data['check_out']
        guests = query.validated_data.get('guests')

        try:
            room_types = get_available_room_types(request.property, check_in, check_out, guests=guests)
        except DjangoValidationError as error:
            raise ValidationError(error.message_dict) from error

        room_types = with_room_type_details(room_types, request.property)
        return Response({
            'check_in': check_in,
            'check_out': check_out,
            'nights': (check_out - check_in).days,
            'guests': guests,
            'room_types': AvailableRoomTypeSerializer(room_types, many=True).data,
        })


class BookingCreateAPIView(PublicPropertyViewMixin, APIView):
    """
    POST /api/p/<property_slug>/bookings/

    Public: guests book without logging in. Room type ids in the body must
    belong to `request.property`; any other id is 404.
    """

    permission_classes = [AllowAny]

    def post(self, request):
        payload = BookingInputSerializer(data=request.data)
        payload.is_valid(raise_exception=True)

        try:
            booking = create_booking(request.property, **payload.validated_data)
        except DjangoValidationError as error:
            raise ValidationError(error.message_dict) from error

        booking = booking_with_details(request.property, booking.pk)
        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)
