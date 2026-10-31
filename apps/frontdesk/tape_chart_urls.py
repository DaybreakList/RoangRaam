from django.urls import path
from .tape_chart_views import TapeChartMoveView, TapeChartView

app_name = 'tape_chart'
urlpatterns = [
    path('', TapeChartView.as_view(), name='index'),
    path('rooms/<int:booking_room_id>/move/', TapeChartMoveView.as_view(), name='move'),
]
