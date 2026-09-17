import json

import pytest

from conftest import SECRET
from webhook_mock_sender.mock_request import build_mock_request
from webhook_mock_sender.providers import PROVIDERS, SignatureVerificationError, get_provider

ALL_EVENTS = [(provider.key, event_name) for provider in PROVIDERS.values() for event_name in provider.events]


@pytest.mark.parametrize(("provider_key", "event_name"), ALL_EVENTS)
def test_every_template_is_signed_with_a_valid_signature(provider_key, event_name):
    provider = get_provider(provider_key)
    mock_request = build_mock_request(provider, event_name, SECRET)

    assert isinstance(json.loads(mock_request.body), dict)
    provider.verify_signature(mock_request.headers, mock_request.body, SECRET)


@pytest.mark.parametrize("provider_key", list(PROVIDERS))
def test_invalid_signature_scenario_fails_verification(provider_key):
    provider = get_provider(provider_key)
    mock_request = build_mock_request(provider, next(iter(provider.events)), SECRET, use_invalid_signature=True)

    assert mock_request.is_signature_valid is False
    with pytest.raises(SignatureVerificationError):
        provider.verify_signature(mock_request.headers, mock_request.body, SECRET)


@pytest.mark.parametrize("provider_key", list(PROVIDERS))
def test_a_changed_body_fails_verification(provider_key):
    provider = get_provider(provider_key)
    mock_request = build_mock_request(provider, next(iter(provider.events)), SECRET)

    with pytest.raises(SignatureVerificationError):
        provider.verify_signature(mock_request.headers, mock_request.body + b" ", SECRET)


def test_github_signature_matches_the_vector_from_github_docs():
    provider = get_provider("github")
    headers = provider.build_headers(
        "push", b"Hello, World!", "It's a Secret to Everybody", timestamp=0, delivery_id="delivery"
    )

    assert headers["X-Hub-Signature-256"] == "sha256=757107ea0eb2509fc211221cce984b8a37570b6d7586c22c46f4379c8b043e17"
    assert headers["X-GitHub-Event"] == "push"
    assert headers["X-GitHub-Delivery"] == "delivery"


def test_shopify_signature_is_base64_and_carries_the_topic():
    provider = get_provider("shopify")
    headers = provider.build_headers("orders/create", b"{}", "secret", timestamp=0, delivery_id="delivery")

    assert headers["X-Shopify-Hmac-Sha256"] == "dzJZAsrKgS3CWXM6rNBGtzgXNyx3e42VtAJkdHRRbhM="
    assert headers["X-Shopify-Topic"] == "orders/create"
    assert headers["X-Shopify-Webhook-Id"] == "delivery"


def test_stripe_signature_is_accepted_by_the_official_stripe_library():
    stripe = pytest.importorskip("stripe")
    mock_request = build_mock_request(get_provider("stripe"), "payment_intent.succeeded", SECRET)

    event = stripe.Webhook.construct_event(mock_request.body, mock_request.headers["Stripe-Signature"], SECRET)

    assert event["type"] == "payment_intent.succeeded"


def test_stripe_rejects_an_old_timestamp_as_a_replay():
    provider = get_provider("stripe")
    mock_request = build_mock_request(provider, "invoice.paid", SECRET, timestamp_offset_seconds=-600)

    with pytest.raises(SignatureVerificationError, match="tolerance"):
        provider.verify_signature(mock_request.headers, mock_request.body, SECRET)
    provider.verify_signature(mock_request.headers, mock_request.body, SECRET, tolerance_seconds=0)


def test_verification_reports_a_missing_header():
    for provider in PROVIDERS.values():
        with pytest.raises(SignatureVerificationError, match="missing"):
            provider.verify_signature({}, b"{}", SECRET)
