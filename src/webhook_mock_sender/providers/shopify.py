import base64
import hashlib
import hmac
import uuid
from collections.abc import Mapping

from webhook_mock_sender.providers.base import (
    DEFAULT_TOLERANCE_SECONDS,
    EventTemplate,
    Provider,
    SignatureVerificationError,
    find_header,
    format_iso_timestamp,
    random_number,
    random_token,
    register_events,
)

SHOPIFY_API_VERSION = "2025-07"
SHOP_DOMAIN = "example-store.myshopify.com"
HEXADECIMAL_DIGITS = "0123456789abcdef"


def shop_timestamp(timestamp: int) -> str:
    return format_iso_timestamp(timestamp, suffix="+00:00")


def build_customer(timestamp: int) -> dict:
    customer_id = random_number(13)
    return {
        "id": customer_id,
        "admin_graphql_api_id": f"gid://shopify/Customer/{customer_id}",
        "email": "jenny.rosen@example.com",
        "created_at": shop_timestamp(timestamp),
        "updated_at": shop_timestamp(timestamp),
        "first_name": "Jenny",
        "last_name": "Rosen",
        "state": "enabled",
        "note": None,
        "verified_email": True,
        "tax_exempt": False,
        "phone": None,
        "currency": "EUR",
        "tags": "",
        "default_address": {
            "first_name": "Jenny",
            "last_name": "Rosen",
            "address1": "Torstrasse 1",
            "city": "Berlin",
            "zip": "10119",
            "country": "Germany",
            "country_code": "DE",
        },
    }


def build_line_item() -> dict:
    line_item_id = random_number(14)
    return {
        "id": line_item_id,
        "admin_graphql_api_id": f"gid://shopify/LineItem/{line_item_id}",
        "title": "Canvas Tote Bag",
        "variant_title": "Natural",
        "sku": "TOTE-NAT-01",
        "quantity": 2,
        "price": "24.50",
        "product_id": random_number(13),
        "variant_id": random_number(14),
        "requires_shipping": True,
        "taxable": True,
        "fulfillment_status": None,
    }


def build_order(
    timestamp: int, *, financial_status: str, fulfillment_status: str | None = None, is_cancelled: bool = False
) -> dict:
    order_id = random_number(13)
    order_number = 1000 + random_number(3)
    return {
        "id": order_id,
        "admin_graphql_api_id": f"gid://shopify/Order/{order_id}",
        "name": f"#{order_number}",
        "order_number": order_number,
        "email": "jenny.rosen@example.com",
        "created_at": shop_timestamp(timestamp),
        "updated_at": shop_timestamp(timestamp),
        "processed_at": shop_timestamp(timestamp),
        "cancelled_at": shop_timestamp(timestamp) if is_cancelled else None,
        "cancel_reason": "customer" if is_cancelled else None,
        "currency": "EUR",
        "subtotal_price": "49.00",
        "total_tax": "9.31",
        "total_discounts": "0.00",
        "total_price": "58.31",
        "financial_status": financial_status,
        "fulfillment_status": fulfillment_status,
        "test": True,
        "token": random_token(32, HEXADECIMAL_DIGITS),
        "tags": "",
        "line_items": [build_line_item()],
        "customer": build_customer(timestamp),
        "shipping_address": {
            "first_name": "Jenny",
            "last_name": "Rosen",
            "address1": "Torstrasse 1",
            "city": "Berlin",
            "zip": "10119",
            "country": "Germany",
            "country_code": "DE",
        },
    }


def build_product(timestamp: int) -> dict:
    product_id = random_number(13)
    variant_id = random_number(14)
    return {
        "id": product_id,
        "admin_graphql_api_id": f"gid://shopify/Product/{product_id}",
        "title": "Canvas Tote Bag",
        "handle": "canvas-tote-bag",
        "body_html": "<p>Heavy cotton canvas, made in Portugal.</p>",
        "vendor": "Example Store",
        "product_type": "Bags",
        "status": "active",
        "tags": "canvas, tote",
        "created_at": shop_timestamp(timestamp),
        "updated_at": shop_timestamp(timestamp),
        "published_at": shop_timestamp(timestamp),
        "variants": [
            {
                "id": variant_id,
                "admin_graphql_api_id": f"gid://shopify/ProductVariant/{variant_id}",
                "product_id": product_id,
                "title": "Natural",
                "price": "24.50",
                "sku": "TOTE-NAT-01",
                "inventory_quantity": 120,
            }
        ],
    }


def build_checkout(timestamp: int) -> dict:
    checkout_token = random_token(32, HEXADECIMAL_DIGITS)
    return {
        "id": random_number(14),
        "token": checkout_token,
        "cart_token": random_token(32, HEXADECIMAL_DIGITS),
        "email": "jenny.rosen@example.com",
        "created_at": shop_timestamp(timestamp),
        "updated_at": shop_timestamp(timestamp),
        "completed_at": None,
        "currency": "EUR",
        "subtotal_price": "49.00",
        "total_tax": "9.31",
        "total_price": "58.31",
        "abandoned_checkout_url": f"https://{SHOP_DOMAIN}/checkouts/{checkout_token}/recover",
        "line_items": [build_line_item()],
    }


def build_shop(timestamp: int) -> dict:
    return {
        "id": random_number(11),
        "name": "Example Store",
        "email": "owner@example.com",
        "domain": SHOP_DOMAIN,
        "myshopify_domain": SHOP_DOMAIN,
        "country_code": "DE",
        "currency": "EUR",
        "plan_name": "basic",
        "created_at": shop_timestamp(timestamp - 365 * 24 * 60 * 60),
        "updated_at": shop_timestamp(timestamp),
    }


class ShopifyProvider(Provider):
    key = "shopify"
    display_name = "Shopify"
    signature_header = "X-Shopify-Hmac-Sha256"
    signature_summary = "<base64 HMAC-SHA256 of the raw body>"
    secret_placeholder = "the app's client secret"
    secret_environment_variables = ("SHOPIFY_WEBHOOK_SECRET", "SHOPIFY_API_SECRET")
    documentation_url = "https://shopify.dev/docs/apps/build/webhooks/subscribe/https#step-2-validate-the-origin-of-your-webhook-to-ensure-its-coming-from-shopify"
    events = register_events(
        EventTemplate(
            "orders/create", "An order was placed.", lambda timestamp: build_order(timestamp, financial_status="pending")
        ),
        EventTemplate(
            "orders/paid", "An order was paid.", lambda timestamp: build_order(timestamp, financial_status="paid")
        ),
        EventTemplate(
            "orders/fulfilled",
            "All items of an order were shipped.",
            lambda timestamp: build_order(timestamp, financial_status="paid", fulfillment_status="fulfilled"),
        ),
        EventTemplate(
            "orders/cancelled",
            "An order was cancelled.",
            lambda timestamp: build_order(timestamp, financial_status="voided", is_cancelled=True),
        ),
        EventTemplate("products/create", "A product was added.", build_product),
        EventTemplate("products/update", "A product was changed.", build_product),
        EventTemplate("customers/create", "A customer registered.", build_customer),
        EventTemplate("checkouts/create", "A checkout was started.", build_checkout),
        EventTemplate("app/uninstalled", "The merchant uninstalled your app.", build_shop),
    )

    def create_delivery_id(self) -> str:
        return str(uuid.uuid4())

    def build_headers(
        self, event_name: str, body: bytes, secret: str, *, timestamp: int, delivery_id: str
    ) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "User-Agent": "Shopify-Captain-Hook",
            "X-Shopify-Topic": event_name,
            "X-Shopify-Hmac-Sha256": compute_shopify_signature(secret, body),
            "X-Shopify-Shop-Domain": SHOP_DOMAIN,
            "X-Shopify-API-Version": SHOPIFY_API_VERSION,
            "X-Shopify-Webhook-Id": delivery_id,
            "X-Shopify-Event-Id": str(uuid.uuid5(uuid.NAMESPACE_URL, delivery_id)),
            "X-Shopify-Triggered-At": format_iso_timestamp(timestamp, suffix=".000000000Z"),
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
        received_signature = find_header(headers, self.signature_header)
        if not received_signature:
            raise SignatureVerificationError("X-Shopify-Hmac-Sha256 header is missing")
        if not hmac.compare_digest(compute_shopify_signature(secret, body), received_signature):
            raise SignatureVerificationError("X-Shopify-Hmac-Sha256 does not match the body and the secret")


def compute_shopify_signature(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode("ascii")
