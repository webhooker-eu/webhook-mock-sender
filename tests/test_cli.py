import json

import pytest

from conftest import SECRET, TARGET_URL
from webhook_mock_sender import cli
from webhook_mock_sender.providers import get_provider


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for variable_name in (
        "WEBHOOK_MOCK_URL",
        "WEBHOOK_MOCK_SECRET",
        "STRIPE_WEBHOOK_SECRET",
        "GITHUB_WEBHOOK_SECRET",
        "SHOPIFY_WEBHOOK_SECRET",
        "SHOPIFY_API_SECRET",
    ):
        monkeypatch.delenv(variable_name, raising=False)


@pytest.fixture
def use_fake_endpoint(monkeypatch, fake_endpoint):
    monkeypatch.setattr(cli, "WebhookSender", lambda **sender_options: fake_endpoint.build_sender())
    return fake_endpoint


def test_send_delivers_a_signed_event(use_fake_endpoint, capsys):
    exit_code = cli.main(["send", "stripe", "payment_intent.succeeded", TARGET_URL, "--secret", SECRET])

    received_request = use_fake_endpoint.requests[0]
    assert exit_code == cli.EXIT_OK
    assert "Delivery: HTTP 200" in capsys.readouterr().out
    get_provider("stripe").verify_signature(received_request.headers, received_request.content, SECRET)


def test_secret_and_url_come_from_the_environment(use_fake_endpoint, monkeypatch):
    monkeypatch.setenv("WEBHOOK_MOCK_URL", TARGET_URL)
    monkeypatch.setenv("WEBHOOK_MOCK_SECRET", "generic-secret")
    monkeypatch.setenv("SHOPIFY_API_SECRET", "shopify-secret")

    assert cli.main(["send", "shopify", "orders/paid"]) == cli.EXIT_OK
    received_request = use_fake_endpoint.requests[0]
    get_provider("shopify").verify_signature(received_request.headers, received_request.content, "shopify-secret")


def test_repeat_sends_the_same_delivery_again(use_fake_endpoint, capsys):
    exit_code = cli.main(["send", "github", "push", TARGET_URL, "--secret", SECRET, "--repeat", "3"])

    delivery_ids = {request.headers["X-GitHub-Delivery"] for request in use_fake_endpoint.requests}
    assert exit_code == cli.EXIT_OK
    assert len(use_fake_endpoint.requests) == 3 and len(delivery_ids) == 1
    assert "Delivery 3/3" in capsys.readouterr().out


def test_expect_turns_a_refused_bad_signature_into_success(use_fake_endpoint):
    use_fake_endpoint.queue(401)
    arguments = ["send", "github", "push", TARGET_URL, "--secret", SECRET, "--invalid-signature", "--expect", "401"]

    assert cli.main(arguments) == cli.EXIT_OK


def test_expect_fails_when_a_bad_signature_is_accepted(use_fake_endpoint, capsys):
    arguments = ["send", "github", "push", TARGET_URL, "--secret", SECRET, "--invalid-signature", "--expect", "401"]

    assert cli.main(arguments) == cli.EXIT_DELIVERY_FAILED
    error_output = capsys.readouterr().err
    assert "expected HTTP 401, got HTTP 200" in error_output
    assert "does not verify signatures" in error_output


def test_failed_delivery_exits_with_one(use_fake_endpoint, capsys):
    use_fake_endpoint.queue(500, "boom")

    assert cli.main(["send", "github", "ping", TARGET_URL, "--secret", SECRET]) == cli.EXIT_DELIVERY_FAILED
    assert "boom" in capsys.readouterr().err


def test_dry_run_prints_the_request_without_sending(use_fake_endpoint, capsys):
    arguments = ["send", "stripe", "invoice.paid", "--secret", SECRET, "--set", "data.object.total=100", "--dry-run", "--json"]

    assert cli.main(arguments) == cli.EXIT_OK
    printed_request = json.loads(capsys.readouterr().out)
    assert json.loads(printed_request["body"])["data"]["object"]["total"] == 100
    assert printed_request["headers"]["Stripe-Signature"].startswith("t=")
    assert use_fake_endpoint.requests == []


def test_curl_prints_a_command(use_fake_endpoint, capsys):
    assert cli.main(["send", "github", "star", TARGET_URL, "--secret", SECRET, "--curl"]) == cli.EXIT_OK
    assert capsys.readouterr().out.startswith(f"curl -X POST {TARGET_URL}")
    assert use_fake_endpoint.requests == []


def test_payload_file_and_header_options(use_fake_endpoint, tmp_path):
    payload_path = tmp_path / "event.json"
    payload_path.write_text('{"action": "labeled"}', encoding="utf-8")
    arguments = ["send", "github", "label", TARGET_URL, "--secret", SECRET]
    arguments += ["--payload-file", str(payload_path), "--header", "X-Trace: abc"]

    assert cli.main(arguments) == cli.EXIT_OK
    received_request = use_fake_endpoint.requests[0]
    assert received_request.content == b'{"action": "labeled"}'
    assert received_request.headers["X-GitHub-Event"] == "label"
    assert received_request.headers["X-Trace"] == "abc"


@pytest.mark.parametrize(
    "arguments",
    [
        ["send", "paypal", "payment", TARGET_URL, "--secret", SECRET],
        ["send", "stripe", "not.an.event", TARGET_URL, "--secret", SECRET],
        ["send", "stripe", "invoice.paid", TARGET_URL],
        ["send", "stripe", "invoice.paid", "--secret", SECRET],
        ["send", "stripe", "invoice.paid", "localhost:3000", "--secret", SECRET],
        ["send", "stripe", "invoice.paid", TARGET_URL, "--secret", SECRET, "--repeat", "0"],
        ["send", "stripe", "invoice.paid", TARGET_URL, "--secret", SECRET, "--header", "no-colon"],
        ["send", "stripe", "invoice.paid", TARGET_URL, "--secret", SECRET, "--set", "broken"],
        ["send", "stripe", "invoice.paid", TARGET_URL, "--secret", SECRET, "--payload-file", "/does/not/exist.json"],
    ],
)
def test_invalid_input_exits_with_two(use_fake_endpoint, arguments, capsys):
    assert cli.main(arguments) == cli.EXIT_INVALID_INPUT
    assert capsys.readouterr().err.startswith("✗ ")
    assert use_fake_endpoint.requests == []


def test_list_shows_every_provider_and_supports_json(capsys):
    assert cli.main(["list"]) == cli.EXIT_OK
    listing = capsys.readouterr().out
    assert "payment_intent.succeeded" in listing and "pull_request" in listing and "orders/create" in listing

    assert cli.main(["list", "shopify", "--json"]) == cli.EXIT_OK
    catalog = json.loads(capsys.readouterr().out)
    assert [provider["key"] for provider in catalog] == ["shopify"]
    assert catalog[0]["signature_header"] == "X-Shopify-Hmac-Sha256"
