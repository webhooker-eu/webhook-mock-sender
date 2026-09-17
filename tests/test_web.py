import json

import pytest
from fastapi.testclient import TestClient

from conftest import SECRET, TARGET_URL
from webhook_mock_sender.providers import get_provider
from webhook_mock_sender.web import create_app


@pytest.fixture
def web_client(fake_endpoint):
    with TestClient(create_app(fake_endpoint.build_sender())) as test_client:
        yield test_client


def test_index_page_and_health_check(web_client):
    index_response = web_client.get("/")
    assert index_response.status_code == 200
    assert "Webhook Mock Sender" in index_response.text
    assert "https://webhooker.eu/" in index_response.text
    assert web_client.get("/healthz").json() == {"status": "ok"}


def test_providers_catalog(web_client):
    catalog = web_client.get("/api/providers").json()

    assert [provider["key"] for provider in catalog] == ["stripe", "github", "shopify"]
    assert {"name": "push", "description": "A commit was pushed to main."} in catalog[1]["events"]


def test_template_returns_the_serialized_body(web_client):
    result = web_client.post("/api/template", json={"provider": "stripe", "event": "charge.refunded"}).json()

    assert result["ok"] is True
    assert json.loads(result["body"])["type"] == "charge.refunded"


def test_preview_signs_the_edited_body(web_client):
    edited_body = '{"id": "evt_custom", "type": "invoice.paid"}'
    result = web_client.post(
        "/api/preview",
        json={"provider": "stripe", "event": "invoice.paid", "secret": SECRET, "body": edited_body, "target_url": TARGET_URL},
    ).json()

    assert result["request"]["body"] == edited_body
    assert result["curl"].startswith(f"curl -X POST {TARGET_URL}")
    get_provider("stripe").verify_signature(result["request"]["headers"], edited_body.encode(), SECRET)


def test_send_delivers_and_repeats(web_client, fake_endpoint):
    fake_endpoint.queue(200, "first")
    fake_endpoint.queue(409, "duplicate")
    result = web_client.post(
        "/api/send",
        json={"provider": "github", "event": "push", "secret": SECRET, "target_url": TARGET_URL, "repeat": 2},
    ).json()

    assert [delivery["status_code"] for delivery in result["deliveries"]] == [200, 409]
    assert len({request.headers["X-GitHub-Delivery"] for request in fake_endpoint.requests}) == 1
    received_request = fake_endpoint.requests[0]
    get_provider("github").verify_signature(received_request.headers, received_request.content, SECRET)


@pytest.mark.parametrize(
    ("changes", "expected_error"),
    [
        ({"provider": "paypal"}, "Unknown provider"),
        ({"event": "not.an.event"}, "no mock event"),
        ({"secret": ""}, "signing secret"),
        ({"body": "{broken"}, "not valid JSON"),
        ({"target_url": "file:///etc/passwd"}, "http:// or https://"),
    ],
)
def test_send_refuses_invalid_input(web_client, fake_endpoint, changes, expected_error):
    send_input = {"provider": "stripe", "event": "invoice.paid", "secret": SECRET, "target_url": TARGET_URL}
    response = web_client.post("/api/send", json={**send_input, **changes})

    assert response.status_code == 400
    assert expected_error in response.json()["error"]
    assert fake_endpoint.requests == []


def test_send_needs_a_json_content_type(web_client, fake_endpoint):
    # A page on another origin can only post "simple" content types without a CORS preflight.
    send_input = {"provider": "stripe", "event": "invoice.paid", "secret": SECRET, "target_url": TARGET_URL}
    response = web_client.post("/api/send", content=json.dumps(send_input), headers={"Content-Type": "text/plain"})

    assert response.status_code == 422
    assert fake_endpoint.requests == []
