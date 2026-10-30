from django.urls import path

from . import views

app_name = 'dashboard'
urlpatterns = [
    path('p/<slug:property_slug>/dashboard/', views.HotelDashboardView.as_view(), name='hotel'),
    path('dashboard/compare/', views.compare, name='compare'),
]
