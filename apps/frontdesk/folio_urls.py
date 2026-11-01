from django.urls import path

from .folio_views import close_view, folio_view, pay_view, receipt_view

app_name = 'folio'
urlpatterns = [
    path('<uuid:booking_id>/', folio_view, name='detail'),
    path('<uuid:booking_id>/pay/', pay_view, name='pay'),
    path('<uuid:booking_id>/close/', close_view, name='close'),
    path('<uuid:booking_id>/receipt.pdf', receipt_view, name='receipt'),
]
