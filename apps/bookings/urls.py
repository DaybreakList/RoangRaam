from django.urls import path

from . import api, guest_views, webhooks
from . import booking_views
from . import manage_views

app_name = 'bookings'
urlpatterns = [
    path('p/<slug:property_slug>/search/', guest_views.search, name='search'),
    path('p/<slug:property_slug>/bookings/<uuid:booking_id>/pay/', guest_views.pay, name='pay'),
    path(
        'p/<slug:property_slug>/bookings/<uuid:booking_id>/payment/',
        guest_views.payment_result,
        name='payment-result',
    ),
    path('p/<slug:property_slug>/book/', booking_views.book, name='book'),
    path('p/<slug:property_slug>/bookings/<uuid:booking_id>/confirmed/', booking_views.booking_confirmed, name='booking-confirmed'),
    path('p/<slug:property_slug>/my-booking/', manage_views.manage_booking, name='manage-booking'),
    path('p/<slug:property_slug>/my-booking/cancel/', manage_views.cancel, name='manage-booking-cancel'),
    path('stripe/webhook/', webhooks.stripe_webhook, name='stripe-webhook'),
    path(
        'api/p/<slug:property_slug>/availability/',
        api.AvailabilityAPIView.as_view(),
        name='api-availability',
    ),
    path(
        'api/p/<slug:property_slug>/bookings/',
        api.BookingCreateAPIView.as_view(),
        name='api-booking-create',
    ),
]
