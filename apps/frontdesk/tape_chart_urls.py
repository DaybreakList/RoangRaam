from django.urls import path
from .tape_chart_views import TapeChartView

app_name = 'tape_chart'
urlpatterns = [path('', TapeChartView.as_view(), name='index')]
