import json

import stripe
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .payments import record_checkout_payment, release_unpaid_booking

PAID_EVENTS = {'checkout.session.completed', 'checkout.session.async_payment_succeeded'}
UNPAID_EVENTS = {'checkout.session.expired', 'checkout.session.async_payment_failed'}


# Stripe posts here without a CSRF token; the signature check replaces it.
@csrf_exempt
@require_POST
def stripe_webhook(request):
    try:
        stripe.WebhookSignature.verify_header(
            request.body.decode('utf-8'),
            request.headers.get('Stripe-Signature'),
            settings.STRIPE_WEBHOOK_SECRET,
            tolerance=stripe.Webhook.DEFAULT_TOLERANCE,
        )
        event = json.loads(request.body)
    except (stripe.SignatureVerificationError, ValueError):
        return HttpResponseBadRequest()

    session = event['data']['object']
    # PromptPay pays later: "completed" arrives unpaid, then async_payment_succeeded
    if event['type'] in PAID_EVENTS and session.get('payment_status') == 'paid':
        record_checkout_payment(event['id'], session)
    elif event['type'] in UNPAID_EVENTS:
        release_unpaid_booking(session)
    # Any other event is answered 200 too, so Stripe does not keep resending it
    return HttpResponse()
