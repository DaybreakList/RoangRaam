from django.urls import path

from . import guest_views

app_name = 'bookings'
urlpatterns = [
    path('p/<slug:property_slug>/search/', guest_views.search, name='search'),
]
