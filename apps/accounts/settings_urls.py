from django.urls import path

from .settings_views import HotelSettingsView

app_name = 'hotel_settings'

urlpatterns = [
    path('', HotelSettingsView.as_view(), name='edit'),
]
