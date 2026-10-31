from django.urls import path

from .check_in_out_views import check_in_view, check_out_view

app_name = 'check_in_out'
urlpatterns = [
    path('<uuid:booking_id>/check-in/', check_in_view, name='check_in'),
    path('<uuid:booking_id>/check-out/', check_out_view, name='check_out'),
]
