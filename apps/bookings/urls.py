from django.urls import path

from . import api, guest_views, webhooks

app_name = 'bookings'
urlpatterns = [
    path('p/<slug:property_slug>/search/', guest_views.search, name='search'),
    path('p/<slug:property_slug>/bookings/<uuid:booking_id>/pay/', guest_views.pay, name='pay'),
    path(
        'p/<slug:property_slug>/bookings/<uuid:booking_id>/payment/',
        guest_views.payment_result,
        name='payment-result',
    ),
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
