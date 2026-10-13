from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from apps.properties.permissions import Role
from .services import INVENTORY, delete_inventory, inventory_queryset, save_inventory

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
