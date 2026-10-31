from django.urls import path

from .room_status_views import RoomStatusView, mark_clean_view

app_name = 'room_status'
urlpatterns = [
    path('', RoomStatusView.as_view(), name='index'),
    path('<int:room_id>/clean/', mark_clean_view, name='mark_clean'),
]
