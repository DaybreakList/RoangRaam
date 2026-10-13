import calendar
from datetime import date
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.core.paginator import Paginator
from django.http import Http404, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .forms import CalendarFilterForm, DailyPriceForm, PriceRangeForm
from .models import DailyRate, RoomType

from apps.properties.permissions import Role
from .services import (
    INVENTORY, delete_inventory, inventory_queryset, monthly_prices,
    save_inventory, set_daily_prices,
)

READ_ROLES = (Role.OWNER, Role.MANAGER, Role.FRONT_DESK)
WRITE_ROLES = (Role.OWNER, Role.MANAGER)


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


def price_calendar(request):
    queryset = RoomType.scoped.for_property(request.property)
    raw_type = request.GET.get('room_type')
    if raw_type:
        try:
            room_type = get_object_or_404(queryset, pk=int(raw_type))
        except (ValueError, OverflowError):
            raise Http404
    else:
        room_type = queryset.first()
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
    prices = monthly_prices(request.property, room_type, month)
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(month.year, month.month):
        cells = []
        for number in week:
            if not number:
                cells.append(None)
                continue
            day = date(month.year, month.month, number)
            cells.append({'day': day, 'has_rate': day in prices, 'price': prices.get(day),
                          'edit_url': reverse('rooms:price-day', kwargs={
                              'property_slug': request.property.slug, 'pk': room_type.pk,
                              'day': day.isoformat(),
                          })})
        weeks.append(cells)
    index = month.year * 12 + month.month - 1
    previous = date((index - 1) // 12, (index - 1) % 12 + 1, 1) if index > 12 else None
    following = date((index + 1) // 12, (index + 1) % 12 + 1, 1) if index < 119999 else None
    return render(request, 'rooms/calendar.html', pricing_context(
        request, filter_form=form, month=month, room_type=room_type, weeks=weeks,
        weekdays=['จ.', 'อ.', 'พ.', 'พฤ.', 'ศ.', 'ส.', 'อา.'],
        previous_url=pricing_url(request, room_type, previous) if previous else None,
        next_url=pricing_url(request, room_type, following) if following else None,
        bulk_url=reverse('rooms:price-bulk', kwargs={
            'property_slug': request.property.slug, 'pk': room_type.pk,
        }),
    ))


def price_edit(request, pk, day=None):
    room_type = get_object_or_404(RoomType.scoped.for_property(request.property), pk=pk)
    selected_day = None
    if day is not None:
        try:
            selected_day = date.fromisoformat(day)
        except ValueError:
            raise Http404
    check_role(request, write=True)
    if request.method not in ('GET', 'HEAD', 'POST'):
        return HttpResponseNotAllowed(['GET', 'HEAD', 'POST'])
    form_class = DailyPriceForm if selected_day else PriceRangeForm
    status = 200
    if request.method == 'POST':
        for field, expected in (('property', request.property.pk), ('property_id', request.property.pk),
                                ('room_type', pk), ('room_type_id', pk)):
            if any(str(value) != str(expected) for value in request.POST.getlist(field)):
                raise Http404
        form = form_class(request.POST)
        if form.is_valid():
            start = selected_day or form.cleaned_data['start_date']
            end = selected_day or form.cleaned_data['end_date']
            try:
                count = set_daily_prices(request.property, pk, start, end, form.cleaned_data['price'])
            except ValidationError as error:
                for field, errors in getattr(error, 'message_dict', {'__all__': error.messages}).items():
                    form.add_error(field if field in form.fields else None, errors)
            except IntegrityError:
                form.add_error(None, 'ข้อมูลราคาเปลี่ยนไประหว่างบันทึก กรุณาลองอีกครั้ง')
            else:
                messages.success(request, f'บันทึกราคาเรียบร้อยแล้ว {count} วัน')
                return redirect(pricing_url(request, room_type, start))
        status = 400
    else:
        initial = {}
        if selected_day:
            rate = DailyRate.scoped.for_property(request.property).filter(
                room_type=room_type, date=selected_day,
            ).first()
            if rate is not None:
                initial['price'] = rate.price
        form = form_class(initial=initial)
    return render(request, 'rooms/price_form.html', pricing_context(
        request, form=form, room_type=room_type, day=selected_day,
        back_url=pricing_url(request, room_type, selected_day),
    ), status=status)
