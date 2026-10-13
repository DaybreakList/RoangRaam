from django.urls import path

from . import views
from .services import INVENTORY

app_name = 'rooms'
urlpatterns = []
for kind in INVENTORY:
    urlpatterns += [
        path(f'{kind}/', views.inventory_list, {'kind': kind}, name=f'{kind}-list'),
        path(f'{kind}/new/', views.inventory_edit, {'kind': kind}, name=f'{kind}-create'),
        path(f'{kind}/<int:pk>/', views.inventory_detail, {'kind': kind}, name=f'{kind}-detail'),
        path(f'{kind}/<int:pk>/edit/', views.inventory_edit, {'kind': kind}, name=f'{kind}-edit'),
        path(f'{kind}/<int:pk>/delete/', views.inventory_delete, {'kind': kind}, name=f'{kind}-delete'),
    ]
