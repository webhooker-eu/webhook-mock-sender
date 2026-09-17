import json
import shlex
import time
from dataclasses import dataclass

from webhook_mock_sender.providers import Provider
from webhook_mock_sender.providers.base import DEFAULT_TOLERANCE_SECONDS

TAMPERED_SECRET_SUFFIX = "-not-the-real-secret"


class InvalidRequestError(ValueError):
    pass


@dataclass(frozen=True)
class MockRequest:
    provider_key: str
    event_name: str
    body: bytes
    headers: dict[str, str]
    is_signature_valid: bool
    is_timestamp_stale: bool = False

    def to_dict(self) -> dict:
        return {
            "provider": self.provider_key,
            "event": self.event_name,
            "headers": self.headers,
            "body": self.body.decode("utf-8"),
            "is_signature_valid": self.is_signature_valid,
            "is_timestamp_stale": self.is_timestamp_stale,
        }


def parse_override(override_argument: str) -> tuple[list[str], object]:
    path_text, separator, value_text = override_argument.partition("=")
    path_segments = [segment for segment in path_text.strip().split(".") if segment]
    if not separator or not path_segments:
        raise InvalidRequestError(f"Invalid override {override_argument!r}: expected 'path.to.key=value'")
    try:
        value = json.loads(value_text)
    except json.JSONDecodeError:
        value = value_text
    return path_segments, value


def apply_override(payload: dict, path_segments: list[str], value: object) -> None:
    """Sets a nested value; numeric segments index into lists, missing objects are created."""
    path_text = ".".join(path_segments)
    container: object = payload
    for segment_index, segment in enumerate(path_segments):
        is_last_segment = segment_index == len(path_segments) - 1
        if isinstance(container, list):
            if not segment.isdecimal() or int(segment) >= len(container):
                raise InvalidRequestError(f"Cannot set {path_text!r}: {segment!r} is not an index of that list")
            key: int | str = int(segment)
        elif isinstance(container, dict):
            key = segment
            if not is_last_segment and container.get(segment) is None:
                container[segment] = {}
        else:
            raise InvalidRequestError(f"Cannot set {path_text!r}: {segment!r} is inside a plain value")
        if is_last_segment:
            container[key] = value
        else:
            container = container[key]


def build_event_payload(
    provider: Provider, event_name: str, *, overrides: list[str] | None = None, current_time: int | None = None
) -> dict:
    event_template = provider.events.get(event_name)
    if event_template is None:
        raise InvalidRequestError(
            f"{provider.display_name} has no mock event {event_name!r}. Available: {', '.join(provider.events)}"
        )
    payload = event_template.build_payload(int(time.time()) if current_time is None else current_time)
    for override_argument in overrides or []:
        apply_override(payload, *parse_override(override_argument))
    return payload


def build_mock_request(
    provider: Provider,
    event_name: str,
    secret: str,
    *,
    body: bytes | None = None,
    overrides: list[str] | None = None,
    timestamp_offset_seconds: int = 0,
    use_invalid_signature: bool = False,
    extra_headers: dict[str, str] | None = None,
    current_time: int | None = None,
) -> MockRequest:
    """Builds the body and the signed headers exactly as the provider would send them.

    A custom body is signed byte for byte. A negative timestamp offset produces a request
    that looks like a replay of an old delivery.
    """
    if not secret:
        raise InvalidRequestError("A signing secret is required")
    current_time = int(time.time()) if current_time is None else current_time
    if body is None:
        payload = build_event_payload(provider, event_name, overrides=overrides, current_time=current_time)
        body = provider.serialize_payload(payload)
    elif overrides:
        raise InvalidRequestError("Overrides cannot be combined with a custom body")

    signing_secret = secret + TAMPERED_SECRET_SUFFIX if use_invalid_signature else secret
    headers = provider.build_headers(
        event_name,
        body,
        signing_secret,
        timestamp=current_time + timestamp_offset_seconds,
        delivery_id=provider.create_delivery_id(),
    )
    for header_name, header_value in (extra_headers or {}).items():
        replaced_names = [name for name in headers if name.lower() == header_name.lower()]
        for replaced_name in replaced_names:
            del headers[replaced_name]
        headers[header_name] = header_value
    return MockRequest(
        provider_key=provider.key,
        event_name=event_name,
        body=body,
        headers=headers,
        is_signature_valid=not use_invalid_signature,
        is_timestamp_stale=provider.signs_timestamp and abs(timestamp_offset_seconds) > DEFAULT_TOLERANCE_SECONDS,
    )


def build_curl_command(target_url: str, mock_request: MockRequest) -> str:
    lines = [f"curl -X POST {shlex.quote(target_url)}"]
    lines += [f"  -H {shlex.quote(f'{name}: {value}')}" for name, value in mock_request.headers.items()]
    lines.append(f"  --data-binary {shlex.quote(mock_request.body.decode('utf-8'))}")
    return " \\\n".join(lines)
