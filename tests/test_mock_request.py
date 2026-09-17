import json

import pytest

from conftest import SECRET, TARGET_URL
from webhook_mock_sender.mock_request import InvalidRequestError, build_curl_command, build_mock_request
from webhook_mock_sender.providers import get_provider


def test_overrides_change_nested_values_and_list_items():
    mock_request = build_mock_request(
        get_provider("stripe"),
        "customer.subscription.created",
        SECRET,
        overrides=["data.object.status=past_due", "data.object.items.data.0.quantity=3", "data.object.metadata.plan=pro"],
    )
    subscription = json.loads(mock_request.body)["data"]["object"]

    assert subscription["status"] == "past_due"
    assert subscription["items"]["data"][0]["quantity"] == 3
    assert subscription["metadata"] == {"plan": "pro"}


@pytest.mark.parametrize("override", ["no-equals-sign", "=value", "data.object.items.data.9.quantity=1", "type.nested=1"])
def test_invalid_overrides_are_rejected(override):
    with pytest.raises(InvalidRequestError):
        build_mock_request(get_provider("stripe"), "customer.subscription.created", SECRET, overrides=[override])


def test_a_custom_body_is_signed_byte_for_byte_and_may_use_any_event_name():
    provider = get_provider("github")
    custom_body = b'{ "action":  "labeled" }'
    mock_request = build_mock_request(provider, "label", SECRET, body=custom_body)

    assert mock_request.body == custom_body
    assert mock_request.headers["X-GitHub-Event"] == "label"
    provider.verify_signature(mock_request.headers, custom_body, SECRET)


def test_unknown_events_and_a_missing_secret_are_rejected():
    with pytest.raises(InvalidRequestError, match="no mock event"):
        build_mock_request(get_provider("shopify"), "orders/teleported", SECRET)
    with pytest.raises(InvalidRequestError, match="secret"):
        build_mock_request(get_provider("shopify"), "orders/create", "")


def test_extra_headers_replace_existing_ones_case_insensitively():
    mock_request = build_mock_request(
        get_provider("shopify"),
        "orders/paid",
        SECRET,
        extra_headers={"x-shopify-shop-domain": "my-store.myshopify.com", "X-Trace": "abc"},
    )

    assert "X-Shopify-Shop-Domain" not in mock_request.headers
    assert mock_request.headers["x-shopify-shop-domain"] == "my-store.myshopify.com"
    assert mock_request.headers["X-Trace"] == "abc"


def test_stripe_timestamp_offset_moves_the_signed_time():
    mock_request = build_mock_request(
        get_provider("stripe"), "invoice.paid", SECRET, timestamp_offset_seconds=-600, current_time=1_800_000_000
    )

    assert mock_request.headers["Stripe-Signature"].startswith("t=1799999400,v1=")
    assert json.loads(mock_request.body)["created"] == 1_800_000_000


def test_curl_command_quotes_the_body_for_the_shell():
    mock_request = build_mock_request(get_provider("github"), "issues", SECRET, body=b'{"title":"it\'s broken"}')
    curl_command = build_curl_command(TARGET_URL, mock_request)

    assert curl_command.startswith(f"curl -X POST {TARGET_URL} \\\n")
    assert "-H 'X-GitHub-Event: issues'" in curl_command
    assert """--data-binary '{"title":"it'"'"'s broken"}'""" in curl_command
