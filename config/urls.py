from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

from apps.accounts.views import property_home

urlpatterns = [
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
    path('p/<slug:property_slug>/settings/', include('apps.accounts.settings_urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
