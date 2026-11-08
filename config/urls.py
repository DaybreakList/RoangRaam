from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include, re_path
from django.views.static import serve

from apps.accounts.views import hotel_list, property_home

urlpatterns = [
    path('', hotel_list, name='hotel_list'),
    path('admin/', admin.site.urls),
    path('accounts/', include('apps.accounts.urls')),
    path('p/<slug:property_slug>/', property_home, name='property_home'),
    path('p/<slug:property_slug>/inventory/', include('apps.rooms.urls')),
    path('', include('apps.bookings.urls')),
    path('', include('apps.dashboard.urls')),
    path('p/<slug:property_slug>/frontdesk/tape-chart/', include('apps.frontdesk.tape_chart_urls')),
    path('p/<slug:property_slug>/frontdesk/today/', include('apps.frontdesk.daily_ops_urls')),
    path('p/<slug:property_slug>/frontdesk/bookings/', include('apps.frontdesk.check_in_out_urls')),
    path('p/<slug:property_slug>/frontdesk/rooms/', include('apps.frontdesk.room_status_urls')),
    path('p/<slug:property_slug>/frontdesk/folio/', include('apps.frontdesk.folio_urls')),
    path('p/<slug:property_slug>/settings/', include('apps.accounts.settings_urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.SERVE_MEDIA:
    # static() adds nothing when DEBUG is off, so the same view is mounted by hand.
    # serve() refuses paths that leave MEDIA_ROOT and lists no directories.
    urlpatterns += [
        re_path(rf'^{settings.MEDIA_URL.lstrip("/")}(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    ]
