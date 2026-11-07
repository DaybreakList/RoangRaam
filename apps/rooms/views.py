from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import CalendarFilterForm, DailyPriceForm, PriceRangeForm
from .models import RoomType

from apps.properties.permissions import Role
from .services import (
    INVENTORY, adjacent_months, daily_price, delete_inventory, inventory_queryset,
    month_grid, monthly_prices, save_inventory, save_prices,
)

READ_ROLES = (Role.OWNER, Role.MANAGER, Role.FRONT_DESK)
WRITE_ROLES = (Role.OWNER, Role.MANAGER)
WEEKDAY_LABELS = ['จ.', 'อ.', 'พ.', 'พฤ.', 'ศ.', 'ส.', 'อา.']


def inventory_url(request, kind, action='list', pk=None):
    kwargs = {'property_slug': request.property.slug}
    if pk is not None:
        kwargs['pk'] = pk
    return reverse(f'rooms:{kind}-{action}', kwargs=kwargs)


def check_role(request, write=False):
    membership = getattr(request, 'membership', None)
    if membership is None or membership.role not in (WRITE_ROLES if write else READ_ROLES):
        raise PermissionDenied


def context(request, kind, **extra):
    return {
        'pricing_url': pricing_url(request),
        'property': request.property,
        'kind': kind,
        'label': INVENTORY[kind][2],
        'can_write': request.membership.role in WRITE_ROLES,
        'list_url': inventory_url(request, kind),
        'create_url': inventory_url(request, kind, 'create'),
        'navigation': [
            {'label': values[2], 'url': inventory_url(request, key), 'active': key == kind}
            for key, values in INVENTORY.items()
        ],
        **extra,
    }


def inventory_list(request, kind):
    check_role(request)
    if request.method not in ('GET', 'HEAD'):
        return HttpResponseNotAllowed(['GET', 'HEAD'])
    page = Paginator(inventory_queryset(kind, request.property), 25).get_page(request.GET.get('page'))
    rows = []
    for obj in page:
        if kind == 'rooms':
            summary = f'{obj.room_type.name} · ชั้น {obj.floor} · {obj.get_hk_status_display()}'
            if not obj.is_active:
                summary += ' · ปิดใช้งาน'
        elif kind == 'room-types':
            names = ', '.join(amenity.name for amenity in obj.amenities.all())
            summary = f'{obj.max_occupancy} คน · {obj.bed_type} · {obj.size_sqm} ตร.ม.'
            if names:
                summary += f' · {names}'
        else:
            summary = ''
        rows.append({'object': obj, 'summary': summary,
                     'detail_url': inventory_url(request, kind, 'detail', obj.pk),
                     'edit_url': inventory_url(request, kind, 'edit', obj.pk)})
    return render(request, 'rooms/list.html', context(request, kind, rows=rows, page=page))


def inventory_detail(request, kind, pk):
    obj = get_object_or_404(inventory_queryset(kind, request.property), pk=pk)
    check_role(request)
    if request.method not in ('GET', 'HEAD'):
        return HttpResponseNotAllowed(['GET', 'HEAD'])
    if kind == 'rooms':
        details = [('เลขห้อง', obj.number), ('ประเภทห้อง', obj.room_type.name),
                   ('ชั้น', obj.floor), ('สถานะความสะอาด', obj.get_hk_status_display()),
                   ('เปิดใช้งาน', 'ใช่' if obj.is_active else 'ไม่')]
    elif kind == 'room-types':
        details = [('ชื่อ', obj.name), ('จำนวนผู้พักสูงสุด', obj.max_occupancy),
                   ('ประเภทเตียง', obj.bed_type), ('ขนาด (ตร.ม.)', obj.size_sqm),
                   ('รายละเอียด', obj.description or '—'),
                   ('สิ่งอำนวยความสะดวก', ', '.join(a.name for a in obj.amenities.all()) or '—')]
    else:
        details = [('ชื่อ', obj.name)]
    return render(request, 'rooms/detail.html', context(
        request, kind, object=obj, details=details,
        edit_url=inventory_url(request, kind, 'edit', pk),
        delete_url=inventory_url(request, kind, 'delete', pk),
    ))


def inventory_edit(request, kind, pk=None):
    obj = None
    if pk is not None:
        obj = get_object_or_404(inventory_queryset(kind, request.property), pk=pk)
    check_role(request, write=True)
    if request.method not in ('GET', 'HEAD', 'POST'):
        return HttpResponseNotAllowed(['GET', 'HEAD', 'POST'])
    status = 200
    if request.method == 'POST':
        form, saved = save_inventory(kind, request.property, request.POST, pk=pk)
        if saved is not None:
            messages.success(request, 'บันทึกข้อมูลเรียบร้อยแล้ว')
            return redirect(inventory_url(request, kind, 'detail', saved.pk))
        status = 400
    else:
        form = INVENTORY[kind][1](property=request.property, instance=obj)
    return render(request, 'rooms/form.html', context(
        request, kind, form=form, object=obj, action_label='แก้ไข' if pk else 'เพิ่ม',
    ), status=status)


def inventory_delete(request, kind, pk):
    obj = get_object_or_404(inventory_queryset(kind, request.property), pk=pk)
    check_role(request, write=True)
    if request.method not in ('GET', 'HEAD', 'POST'):
        return HttpResponseNotAllowed(['GET', 'HEAD', 'POST'])
    error = None
    if request.method == 'POST':
        error = delete_inventory(kind, request.property, pk)
        if error is None:
            messages.success(request, 'ลบข้อมูลเรียบร้อยแล้ว')
            return redirect(inventory_url(request, kind))
    return render(request, 'rooms/delete.html', context(
        request, kind, object=obj, error=error,
    ), status=409 if error else 200)


def pricing_url(request, room_type=None, month=None):
    url = reverse('rooms:price-calendar', kwargs={'property_slug': request.property.slug})
    params = {}
    if room_type is not None:
        params['room_type'] = room_type.pk
    if month is not None:
        params['month'] = f'{month.year:04d}-{month.month:02d}'
    return url + ('?' + urlencode(params) if params else '')


def pricing_context(request, **extra):
    return {**context(request, 'room-types'), 'label': 'ปฏิทินราคา', 'kind': 'pricing', **extra}


def selected_room_type(request):
    """The room type in ?room_type=, else the hotel's first one; a bad or foreign id is a 404."""
    queryset = RoomType.scoped.for_property(request.property)
    raw_type = request.GET.get('room_type')
    if not raw_type:
        return queryset.first()
    try:
        return get_object_or_404(queryset, pk=int(raw_type))
    except (ValueError, OverflowError):
        raise Http404


def parse_day(day):
    try:
        return date.fromisoformat(day)
    except ValueError:
        raise Http404


def reject_foreign_ids(request, pk):
    """A posted hotel or room type id that differs from the URL is a 404, not a write."""
    for field, expected in (('property', request.property.pk), ('property_id', request.property.pk),
                            ('room_type', pk), ('room_type_id', pk)):
        if any(str(value) != str(expected) for value in request.POST.getlist(field)):
            raise Http404


def price_calendar(request):
    room_type = selected_room_type(request)
    check_role(request)
    if request.method not in ('GET', 'HEAD'):
        return HttpResponseNotAllowed(['GET', 'HEAD'])
    if room_type is None:
        return render(request, 'rooms/calendar.html', pricing_context(request))
    form = CalendarFilterForm(data={
        'room_type': room_type.pk,
        'month': request.GET.get('month', timezone.localdate().strftime('%Y-%m')),
    }, property=request.property)
    if not form.is_valid():
        return render(request, 'rooms/calendar.html', pricing_context(request, filter_form=form), status=400)
    month = form.cleaned_data['month']
    previous, following = adjacent_months(month)
    return render(request, 'rooms/calendar.html', pricing_context(
        request, filter_form=form, month=month, room_type=room_type,
        weeks=month_grid(month, monthly_prices(request.property, room_type, month)),
        weekdays=WEEKDAY_LABELS,
        previous_url=pricing_url(request, room_type, previous) if previous else None,
        next_url=pricing_url(request, room_type, following) if following else None,
        bulk_url=reverse('rooms:price-bulk', kwargs={
            'property_slug': request.property.slug, 'pk': room_type.pk,
        }),
    ))


def price_edit(request, pk, day=None):
    room_type = get_object_or_404(RoomType.scoped.for_property(request.property), pk=pk)
    selected_day = None if day is None else parse_day(day)
    check_role(request, write=True)
    if request.method not in ('GET', 'HEAD', 'POST'):
        return HttpResponseNotAllowed(['GET', 'HEAD', 'POST'])
    form_class = DailyPriceForm if selected_day else PriceRangeForm
    status = 200
    if request.method == 'POST':
        reject_foreign_ids(request, pk)
        form = form_class(request.POST)
        saved = save_prices(request.property, pk, form, selected_day)
        if saved is not None:
            start, count = saved
            messages.success(request, f'บันทึกราคาเรียบร้อยแล้ว {count} วัน')
            return redirect(pricing_url(request, room_type, start))
        status = 400
    else:
        # A price of 0 is a real price, so compare with None
        price = daily_price(request.property, room_type, selected_day) if selected_day else None
        form = form_class(initial={} if price is None else {'price': price})
    return render(request, 'rooms/price_form.html', pricing_context(
        request, form=form, room_type=room_type, day=selected_day,
        back_url=pricing_url(request, room_type, selected_day),
    ), status=status)
