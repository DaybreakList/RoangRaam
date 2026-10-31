from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from apps.properties.permissions import Role, role_required

from .check_in_out_forms import CheckInForm
from .services.check_in_out import check_in, check_out

# Same access as the Tape Chart and the daily lists
FRONT_DESK_ROLES = (Role.OWNER, Role.MANAGER, Role.FRONT_DESK)


def _back_to_today(request):
    return redirect('daily_ops:index', property_slug=request.property.slug)


@require_POST
@role_required(*FRONT_DESK_ROLES)
def check_in_view(request, booking_id):
    form = CheckInForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return _back_to_today(request)
    data = form.cleaned_data
    try:
        booking = check_in(
            request.property, booking_id,
            user=request.user,
            id_type=data['id_type'],
            id_number=data['id_number'],
            nationality=data['nationality'],
            room_changes=data['room_changes'],
        )
    except ValidationError as error:
        for message in error.messages:
            messages.error(request, message)
    else:
        messages.success(request, f'เช็คอินการจอง {booking.code} เรียบร้อยแล้ว')
    return _back_to_today(request)


@require_POST
@role_required(*FRONT_DESK_ROLES)
def check_out_view(request, booking_id):
    try:
        booking = check_out(request.property, booking_id)
    except ValidationError as error:
        for message in error.messages:
            messages.error(request, message)
    else:
        messages.success(request, f'เช็คเอาต์การจอง {booking.code} เรียบร้อยแล้ว ห้องถูกเปลี่ยนเป็นรอทำความสะอาด')
    return _back_to_today(request)
