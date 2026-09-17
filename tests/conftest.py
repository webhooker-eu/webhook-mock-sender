import httpx
import pytest

from webhook_mock_sender.sender import WebhookSender

TARGET_URL = "http://localhost:3000/webhooks"
SECRET = "whsec_test_secret"


class FakeEndpoint:
    """Records requests and answers them with queued responses."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.responses: list[httpx.Response | Exception] = []

    def queue(self, status_code: int, text: str = "") -> None:
        self.responses.append(httpx.Response(status_code, text=text))

    def queue_error(self, error: Exception) -> None:
        self.responses.append(error)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self.responses.pop(0) if self.responses else httpx.Response(200, text="ok")
        if isinstance(response, Exception):
            raise response
        return response

    def build_sender(self) -> WebhookSender:
        return WebhookSender(transport=httpx.MockTransport(self.handle))


@pytest.fixture
def fake_endpoint() -> FakeEndpoint:
    return FakeEndpoint()
