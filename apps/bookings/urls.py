from django.urls import path

from . import api, guest_views

app_name = 'bookings'
urlpatterns = [
    path('p/<slug:property_slug>/search/', guest_views.search, name='search'),
    path(
        'api/p/<slug:property_slug>/availability/',
        api.AvailabilityAPIView.as_view(),
        name='api-availability',
    ),
]
