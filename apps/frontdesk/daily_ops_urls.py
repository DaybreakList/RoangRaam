from django.urls import path

from .daily_ops_views import DailyOpsView

app_name = 'daily_ops'
urlpatterns = [path('', DailyOpsView.as_view(), name='index')]
