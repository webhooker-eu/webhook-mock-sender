import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from webhook_mock_sender.mock_request import InvalidRequestError, MockRequest

MAX_RESPONSE_BODY_CHARACTERS = 2000
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


@dataclass(frozen=True)
class DeliveryResult:
    ok: bool
    status_code: int | None
    elapsed_ms: int
    response_body: str = ""
    response_headers: dict[str, str] | None = None
    error: str | None = None
    hint: str | None = None

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "status_code": self.status_code,
            "elapsed_ms": self.elapsed_ms,
            "response_body": self.response_body,
            "response_headers": self.response_headers or {},
            "error": self.error,
            "hint": self.hint,
        }


def validate_target_url(target_url: str) -> str:
    target_url = target_url.strip()
    try:
        parsed_url = urlsplit(target_url)
        hostname = parsed_url.hostname
    except ValueError:
        hostname = None
    if not hostname or parsed_url.scheme not in ("http", "https"):
        raise InvalidRequestError("The endpoint must be an http:// or https:// URL, like http://localhost:3000/webhooks")
    return target_url


def explain_status(status_code: int, mock_request: MockRequest) -> str | None:
    if 200 <= status_code < 300:
        if not mock_request.is_signature_valid:
            return "Your endpoint accepted a request with a wrong signature: it does not verify signatures."
        if mock_request.is_timestamp_stale:
            return "Your endpoint accepted a request signed long ago: it does not check the timestamp, so replays get in."
        return None
    if 300 <= status_code < 400:
        return "Providers do not follow redirects: register the final URL, including https and a trailing slash."
    if status_code in (400, 401, 403):
        if not mock_request.is_signature_valid:
            return "Expected: the signature was wrong on purpose and your endpoint refused it."
        if mock_request.is_timestamp_stale:
            return "Expected: the signed time was too old on purpose and your endpoint refused the replay."
        return (
            "If this is a signature error, check that the endpoint uses the same secret and "
            "verifies the raw request body, before any JSON parsing."
        )
    if status_code == 404:
        return "Nothing handles this path: check the route of your webhook endpoint."
    if status_code == 405:
        return "The endpoint does not accept POST requests."
    if status_code == 415:
        return "The endpoint refused the Content-Type: webhooks arrive as application/json."
    if status_code >= 500:
        return "The handler crashed: look at your application's logs for the stack trace."
    return None


def explain_connection_error(http_error: httpx.HTTPError, target_url: str) -> str | None:
    hostname = urlsplit(target_url).hostname or ""
    if isinstance(http_error, httpx.TimeoutException):
        return "The endpoint did not answer in time. Providers expect a 2xx within a few seconds: do slow work in a queue."
    if isinstance(http_error, httpx.ConnectError) and hostname in LOCAL_HOSTNAMES:
        return (
            "Nothing is listening there. If this tool runs in Docker, localhost is the container itself: "
            "use http://host.docker.internal:<port> instead."
        )
    if isinstance(http_error, httpx.ConnectError):
        return "Could not connect: check the host name, the port and that the server is running."
    return None


class WebhookSender:
    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        verify_tls: bool = True,
        transport: httpx.BaseTransport | None = None,
    ):
        # Redirects are not followed, the same as Stripe, GitHub and Shopify behave.
        self._http = httpx.Client(timeout=timeout_seconds, verify=verify_tls, transport=transport, follow_redirects=False)

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "WebhookSender":
        return self

    def __exit__(self, *exception_info) -> None:
        self.close()

    def send(self, target_url: str, mock_request: MockRequest) -> DeliveryResult:
        target_url = validate_target_url(target_url)
        started_at = time.monotonic()
        try:
            response = self._http.post(target_url, content=mock_request.body, headers=mock_request.headers)
        except httpx.HTTPError as http_error:
            return DeliveryResult(
                ok=False,
                status_code=None,
                elapsed_ms=round((time.monotonic() - started_at) * 1000),
                error=f"Could not reach the endpoint: {str(http_error) or type(http_error).__name__}",
                hint=explain_connection_error(http_error, target_url),
            )
        response_text = response.text
        if len(response_text) > MAX_RESPONSE_BODY_CHARACTERS:
            response_text = response_text[:MAX_RESPONSE_BODY_CHARACTERS] + "…"
        return DeliveryResult(
            ok=response.is_success,
            status_code=response.status_code,
            elapsed_ms=round((time.monotonic() - started_at) * 1000),
            response_body=response_text,
            response_headers=dict(response.headers),
            error=None if response.is_success else f"The endpoint answered {response.status_code} {response.reason_phrase}",
            hint=explain_status(response.status_code, mock_request),
        )
