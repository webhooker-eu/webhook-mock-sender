import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from webhook_mock_sender.mock_request import (
    InvalidRequestError,
    MockRequest,
    build_curl_command,
    build_event_payload,
    build_mock_request,
)
from webhook_mock_sender.providers import PROVIDERS, UnknownProviderError, get_provider
from webhook_mock_sender.sender import WebhookSender, validate_target_url

STATIC_DIRECTORY = Path(__file__).parent / "static"
INDEX_PAGE_PATH = STATIC_DIRECTORY / "index.html"
TARGET_URL_PLACEHOLDER = "$WEBHOOK_MOCK_URL"
MAX_BODY_CHARACTERS = 1_000_000
MAX_TIMESTAMP_OFFSET_SECONDS = 10 * 365 * 24 * 60 * 60


class TemplateInput(BaseModel):
    provider: str = Field(max_length=50)
    event: str = Field(min_length=1, max_length=100)


class MockRequestInput(TemplateInput):
    secret: str = Field(max_length=500)
    body: str | None = Field(default=None, max_length=MAX_BODY_CHARACTERS)
    target_url: str = Field(default="", max_length=2000)
    use_invalid_signature: bool = False
    timestamp_offset_seconds: int = Field(
        default=0, ge=-MAX_TIMESTAMP_OFFSET_SECONDS, le=MAX_TIMESTAMP_OFFSET_SECONDS
    )


class SendInput(MockRequestInput):
    repeat: int = Field(default=1, ge=1, le=20)


def invalid_input_response(error: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": error}, status_code=400)


def build_request_from_input(request_input: MockRequestInput) -> MockRequest:
    if request_input.body is not None:
        try:
            json.loads(request_input.body)
        except json.JSONDecodeError as json_error:
            raise InvalidRequestError(f"The body is not valid JSON: {json_error}") from None
    if not request_input.secret:
        raise InvalidRequestError("Enter the signing secret your endpoint verifies against")
    return build_mock_request(
        get_provider(request_input.provider),
        request_input.event,
        request_input.secret,
        body=request_input.body.encode("utf-8") if request_input.body is not None else None,
        timestamp_offset_seconds=request_input.timestamp_offset_seconds,
        use_invalid_signature=request_input.use_invalid_signature,
    )


router = APIRouter()


@router.get("/", include_in_schema=False)
def index_page() -> FileResponse:
    return FileResponse(INDEX_PAGE_PATH, headers={"Cache-Control": "no-cache"})


@router.get("/healthz", include_in_schema=False)
def health_check() -> dict:
    return {"status": "ok"}


@router.get("/api/providers")
def list_providers() -> list[dict]:
    return [provider.describe() for provider in PROVIDERS.values()]


@router.post("/api/template")
def build_template(template_input: TemplateInput):
    try:
        provider = get_provider(template_input.provider)
        payload = build_event_payload(provider, template_input.event)
    except (UnknownProviderError, InvalidRequestError) as input_error:
        return invalid_input_response(str(input_error))
    return {"ok": True, "body": provider.serialize_payload(payload).decode("utf-8")}


@router.post("/api/preview")
def preview_request(request_input: MockRequestInput):
    try:
        mock_request = build_request_from_input(request_input)
    except (UnknownProviderError, InvalidRequestError) as input_error:
        return invalid_input_response(str(input_error))
    target_url = request_input.target_url.strip() or TARGET_URL_PLACEHOLDER
    return {"ok": True, "request": mock_request.to_dict(), "curl": build_curl_command(target_url, mock_request)}


@router.post("/api/send")
def send_request(send_input: SendInput, request: Request):
    try:
        target_url = validate_target_url(send_input.target_url)
        mock_request = build_request_from_input(send_input)
    except (UnknownProviderError, InvalidRequestError) as input_error:
        return invalid_input_response(str(input_error))
    webhook_sender: WebhookSender = request.app.state.webhook_sender
    deliveries = [webhook_sender.send(target_url, mock_request).to_dict() for _ in range(send_input.repeat)]
    return {"ok": True, "request": mock_request.to_dict(), "deliveries": deliveries}


def create_app(webhook_sender: WebhookSender | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.webhook_sender = webhook_sender or WebhookSender()
        try:
            yield
        finally:
            application.state.webhook_sender.close()

    application = FastAPI(title="Webhook Mock Sender", lifespan=lifespan)
    application.include_router(router)
    application.mount("/static", StaticFiles(directory=STATIC_DIRECTORY), name="static")
    return application
