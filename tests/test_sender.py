import httpx
import pytest

from conftest import SECRET, TARGET_URL
from webhook_mock_sender.mock_request import InvalidRequestError, build_mock_request
from webhook_mock_sender.providers import get_provider


def build_request(**options):
    return build_mock_request(get_provider("github"), "push", SECRET, **options)


def test_send_posts_the_exact_body_and_headers(fake_endpoint):
    mock_request = build_request()
    with fake_endpoint.build_sender() as sender:
        result = sender.send(TARGET_URL, mock_request)

    received_request = fake_endpoint.requests[0]
    assert result.ok and result.status_code == 200 and result.response_body == "ok"
    assert received_request.method == "POST" and str(received_request.url) == TARGET_URL
    assert received_request.content == mock_request.body
    assert received_request.headers["X-Hub-Signature-256"] == mock_request.headers["X-Hub-Signature-256"]
    assert received_request.headers["User-Agent"].startswith("GitHub-Hookshot/")


def test_rejected_signature_gets_a_hint_about_the_raw_body(fake_endpoint):
    fake_endpoint.queue(401, "invalid signature")
    result = fake_endpoint.build_sender().send(TARGET_URL, build_request())

    assert not result.ok and result.status_code == 401
    assert result.error == "The endpoint answered 401 Unauthorized"
    assert "raw request body" in result.hint


def test_endpoint_that_accepts_a_wrong_signature_is_called_out(fake_endpoint):
    result = fake_endpoint.build_sender().send(TARGET_URL, build_request(use_invalid_signature=True))

    assert result.ok
    assert "does not verify signatures" in result.hint


def test_refusing_a_wrong_signature_is_reported_as_expected(fake_endpoint):
    fake_endpoint.queue(400)
    result = fake_endpoint.build_sender().send(TARGET_URL, build_request(use_invalid_signature=True))

    assert result.hint.startswith("Expected")


def test_stale_stripe_timestamp_is_explained(fake_endpoint):
    stale_request = build_mock_request(get_provider("stripe"), "invoice.paid", SECRET, timestamp_offset_seconds=-600)
    fake_endpoint.queue(400)
    sender = fake_endpoint.build_sender()

    assert "refused the replay" in sender.send(TARGET_URL, stale_request).hint
    assert "does not check the timestamp" in sender.send(TARGET_URL, stale_request).hint
    assert build_request(timestamp_offset_seconds=-600).is_timestamp_stale is False


def test_redirects_are_not_followed(fake_endpoint):
    fake_endpoint.responses.append(httpx.Response(301, headers={"Location": "https://example.com/webhooks/"}))
    result = fake_endpoint.build_sender().send(TARGET_URL, build_request())

    assert result.status_code == 301 and len(fake_endpoint.requests) == 1
    assert "do not follow redirects" in result.hint


def test_connection_errors_mention_docker_for_localhost(fake_endpoint):
    fake_endpoint.queue_error(httpx.ConnectError("Connection refused"))
    result = fake_endpoint.build_sender().send(TARGET_URL, build_request())

    assert not result.ok and result.status_code is None
    assert "Connection refused" in result.error
    assert "host.docker.internal" in result.hint


def test_long_responses_are_truncated(fake_endpoint):
    fake_endpoint.queue(500, "x" * 5000)
    result = fake_endpoint.build_sender().send(TARGET_URL, build_request())

    assert len(result.response_body) == 2001 and result.response_body.endswith("…")


@pytest.mark.parametrize("target_url", ["", "localhost:3000", "ftp://example.com/hook", "file:///etc/passwd", "http://"])
def test_only_http_urls_are_accepted(fake_endpoint, target_url):
    with pytest.raises(InvalidRequestError):
        fake_endpoint.build_sender().send(target_url, build_request())
    assert fake_endpoint.requests == []
