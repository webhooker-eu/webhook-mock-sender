import hashlib
import hmac
import json
import time
from collections.abc import Mapping

from webhook_mock_sender.providers.base import (
    DEFAULT_TOLERANCE_SECONDS,
    EventTemplate,
    Provider,
    SignatureVerificationError,
    find_header,
    random_token,
    register_events,
)

STRIPE_API_VERSION = "2025-03-31.basil"
SECONDS_IN_MONTH = 30 * 24 * 60 * 60


def stripe_id(prefix: str, length: int = 24) -> str:
    return f"{prefix}_{random_token(length)}"


def build_customer(timestamp: int) -> dict:
    return {
        "id": stripe_id("cus", 14),
        "object": "customer",
        "address": None,
        "balance": 0,
        "created": timestamp,
        "currency": None,
        "delinquent": False,
        "description": "Mock customer",
        "email": "jenny.rosen@example.com",
        "invoice_prefix": random_token(8).upper(),
        "livemode": False,
        "metadata": {},
        "name": "Jenny Rosen",
        "phone": None,
        "preferred_locales": [],
        "tax_exempt": "none",
    }


def build_payment_intent(timestamp: int, *, status: str) -> dict:
    payment_intent_id = stripe_id("pi")
    is_successful = status == "succeeded"
    return {
        "id": payment_intent_id,
        "object": "payment_intent",
        "amount": 2000,
        "amount_capturable": 0,
        "amount_received": 2000 if is_successful else 0,
        "capture_method": "automatic_async",
        "client_secret": f"{payment_intent_id}_secret_{random_token(25)}",
        "confirmation_method": "automatic",
        "created": timestamp,
        "currency": "eur",
        "customer": stripe_id("cus", 14),
        "description": "Mock payment",
        "last_payment_error": None
        if is_successful
        else {
            "code": "card_declined",
            "decline_code": "insufficient_funds",
            "message": "Your card has insufficient funds.",
            "type": "card_error",
        },
        "latest_charge": stripe_id("ch"),
        "livemode": False,
        "metadata": {"order_id": "6735"},
        "payment_method": stripe_id("pm"),
        "payment_method_types": ["card"],
        "receipt_email": "jenny.rosen@example.com",
        "status": status,
    }


def build_charge(timestamp: int, *, is_refunded: bool) -> dict:
    charge_id = stripe_id("ch")
    return {
        "id": charge_id,
        "object": "charge",
        "amount": 2000,
        "amount_captured": 2000,
        "amount_refunded": 2000 if is_refunded else 0,
        "balance_transaction": stripe_id("txn"),
        "billing_details": {"email": "jenny.rosen@example.com", "name": "Jenny Rosen", "phone": None},
        "captured": True,
        "created": timestamp,
        "currency": "eur",
        "customer": stripe_id("cus", 14),
        "description": "Mock payment",
        "livemode": False,
        "metadata": {"order_id": "6735"},
        "outcome": {
            "network_status": "approved_by_network",
            "risk_level": "normal",
            "seller_message": "Payment complete.",
            "type": "authorized",
        },
        "paid": True,
        "payment_intent": stripe_id("pi"),
        "payment_method": stripe_id("pm"),
        "payment_method_details": {
            "card": {"brand": "visa", "country": "DE", "exp_month": 12, "exp_year": 2030, "last4": "4242"},
            "type": "card",
        },
        "receipt_url": f"https://pay.stripe.com/receipts/payment/{random_token(40)}",
        "refunded": is_refunded,
        "status": "succeeded",
    }


def build_checkout_session(timestamp: int) -> dict:
    return {
        "id": f"cs_test_{random_token(58)}",
        "object": "checkout.session",
        "amount_subtotal": 4900,
        "amount_total": 4900,
        "cancel_url": "https://example.com/cancel",
        "client_reference_id": "user_1842",
        "created": timestamp,
        "currency": "eur",
        "customer": stripe_id("cus", 14),
        "customer_details": {"email": "jenny.rosen@example.com", "name": "Jenny Rosen"},
        "expires_at": timestamp + 24 * 60 * 60,
        "livemode": False,
        "metadata": {"plan": "pro"},
        "mode": "subscription",
        "payment_intent": None,
        "payment_status": "paid",
        "status": "complete",
        "subscription": stripe_id("sub"),
        "success_url": "https://example.com/success",
    }


def build_subscription(timestamp: int, *, status: str) -> dict:
    is_canceled = status == "canceled"
    return {
        "id": stripe_id("sub"),
        "object": "subscription",
        "cancel_at_period_end": False,
        "canceled_at": timestamp if is_canceled else None,
        "collection_method": "charge_automatically",
        "created": timestamp - SECONDS_IN_MONTH if is_canceled else timestamp,
        "currency": "eur",
        "customer": stripe_id("cus", 14),
        "ended_at": timestamp if is_canceled else None,
        "items": {
            "object": "list",
            "data": [
                {
                    "id": stripe_id("si", 14),
                    "object": "subscription_item",
                    "current_period_start": timestamp,
                    "current_period_end": timestamp + SECONDS_IN_MONTH,
                    "price": {
                        "id": stripe_id("price"),
                        "object": "price",
                        "currency": "eur",
                        "product": stripe_id("prod", 14),
                        "recurring": {"interval": "month", "interval_count": 1},
                        "unit_amount": 4900,
                    },
                    "quantity": 1,
                }
            ],
        },
        "latest_invoice": stripe_id("in"),
        "livemode": False,
        "metadata": {},
        "start_date": timestamp,
        "status": status,
    }


def build_invoice(timestamp: int, *, is_paid: bool) -> dict:
    invoice_id = stripe_id("in")
    return {
        "id": invoice_id,
        "object": "invoice",
        "amount_due": 4900,
        "amount_paid": 4900 if is_paid else 0,
        "amount_remaining": 0 if is_paid else 4900,
        "attempt_count": 1,
        "attempted": True,
        "billing_reason": "subscription_cycle",
        "collection_method": "charge_automatically",
        "created": timestamp,
        "currency": "eur",
        "customer": stripe_id("cus", 14),
        "customer_email": "jenny.rosen@example.com",
        "hosted_invoice_url": f"https://invoice.stripe.com/i/acct_mock/{random_token(32)}",
        "livemode": False,
        "next_payment_attempt": None if is_paid else timestamp + 3 * 24 * 60 * 60,
        "number": f"MOCK-{random_token(4).upper()}-0001",
        "period_start": timestamp - SECONDS_IN_MONTH,
        "period_end": timestamp,
        "status": "paid" if is_paid else "open",
        "subtotal": 4900,
        "total": 4900,
    }


def stripe_event(event_type: str, description: str, build_object, previous_attributes: dict | None = None):
    def build_payload(timestamp: int) -> dict:
        event_data = {"object": build_object(timestamp)}
        if previous_attributes is not None:
            event_data["previous_attributes"] = previous_attributes
        return {
            "id": stripe_id("evt"),
            "object": "event",
            "api_version": STRIPE_API_VERSION,
            "created": timestamp,
            "data": event_data,
            "livemode": False,
            "pending_webhooks": 1,
            "request": {"id": stripe_id("req", 14), "idempotency_key": None},
            "type": event_type,
        }

    return EventTemplate(name=event_type, description=description, build_payload=build_payload)


class StripeProvider(Provider):
    key = "stripe"
    display_name = "Stripe"
    signature_header = "Stripe-Signature"
    signature_summary = "t=<unix time>,v1=<hex HMAC-SHA256 of '<time>.<raw body>'>"
    secret_placeholder = "whsec_…"
    secret_environment_variables = ("STRIPE_WEBHOOK_SECRET",)
    documentation_url = "https://docs.stripe.com/webhooks#verify-manually"
    signs_timestamp = True
    events = register_events(
        stripe_event(
            "payment_intent.succeeded",
            "A payment was collected.",
            lambda timestamp: build_payment_intent(timestamp, status="succeeded"),
        ),
        stripe_event(
            "payment_intent.payment_failed",
            "A payment attempt was declined.",
            lambda timestamp: build_payment_intent(timestamp, status="requires_payment_method"),
        ),
        stripe_event(
            "charge.succeeded", "A charge went through.", lambda timestamp: build_charge(timestamp, is_refunded=False)
        ),
        stripe_event(
            "charge.refunded", "A charge was refunded in full.", lambda timestamp: build_charge(timestamp, is_refunded=True)
        ),
        stripe_event("checkout.session.completed", "A customer finished Checkout.", build_checkout_session),
        stripe_event("customer.created", "A customer was created.", build_customer),
        stripe_event(
            "customer.subscription.created",
            "A subscription started.",
            lambda timestamp: build_subscription(timestamp, status="active"),
        ),
        stripe_event(
            "customer.subscription.updated",
            "A subscription moved from trial to active.",
            lambda timestamp: build_subscription(timestamp, status="active"),
            previous_attributes={"status": "trialing"},
        ),
        stripe_event(
            "customer.subscription.deleted",
            "A subscription was canceled.",
            lambda timestamp: build_subscription(timestamp, status="canceled"),
        ),
        stripe_event("invoice.paid", "An invoice was paid.", lambda timestamp: build_invoice(timestamp, is_paid=True)),
        stripe_event(
            "invoice.payment_failed",
            "An invoice payment failed and will be retried.",
            lambda timestamp: build_invoice(timestamp, is_paid=False),
        ),
    )

    def serialize_payload(self, payload: dict) -> bytes:
        return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")

    def create_delivery_id(self) -> str:
        # Stripe identifies a delivery by the event id inside the body, not by a header.
        return ""

    def build_headers(
        self, event_name: str, body: bytes, secret: str, *, timestamp: int, delivery_id: str
    ) -> dict[str, str]:
        return {
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "Stripe/1.0 (+https://stripe.com/docs/webhooks)",
            "Stripe-Signature": f"t={timestamp},v1={compute_stripe_signature(secret, timestamp, body)}",
        }

    def verify_signature(
        self,
        headers: Mapping[str, str],
        body: bytes,
        secret: str,
        *,
        tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
        current_time: int | None = None,
    ) -> None:
        signature_header = find_header(headers, self.signature_header)
        if not signature_header:
            raise SignatureVerificationError("Stripe-Signature header is missing")
        header_items = [item.split("=", 1) for item in signature_header.split(",") if "=" in item]
        timestamps = [value for key, value in header_items if key.strip() == "t"]
        signatures = [value for key, value in header_items if key.strip() == "v1"]
        if not timestamps or not timestamps[0].isdecimal() or not signatures:
            raise SignatureVerificationError("Stripe-Signature header must look like t=<time>,v1=<signature>")
        signed_timestamp = int(timestamps[0])
        expected_signature = compute_stripe_signature(secret, signed_timestamp, body)
        if not any(hmac.compare_digest(expected_signature, signature) for signature in signatures):
            raise SignatureVerificationError("No v1 signature matches the body and the secret")
        current_time = int(time.time()) if current_time is None else current_time
        if tolerance_seconds and abs(current_time - signed_timestamp) > tolerance_seconds:
            raise SignatureVerificationError(f"Timestamp is outside the {tolerance_seconds} second tolerance")


def compute_stripe_signature(secret: str, timestamp: int, body: bytes) -> str:
    signed_payload = f"{timestamp}.".encode() + body
    return hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
