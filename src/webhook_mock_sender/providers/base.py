import json
import secrets
import string
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_TOLERANCE_SECONDS = 300
LOWERCASE_ALPHANUMERIC = string.ascii_lowercase + string.digits
MIXED_CASE_ALPHANUMERIC = string.ascii_letters + string.digits


class SignatureVerificationError(Exception):
    pass


@dataclass(frozen=True)
class EventTemplate:
    name: str
    description: str
    build_payload: Callable[[int], dict]


def random_token(length: int, alphabet: str = MIXED_CASE_ALPHANUMERIC) -> str:
    return "".join(secrets.choice(alphabet) for _ in range(length))


def random_number(digit_count: int) -> int:
    lowest_value = 10 ** (digit_count - 1)
    return lowest_value + secrets.randbelow(9 * lowest_value)


def format_iso_timestamp(timestamp: int, *, suffix: str = "Z") -> str:
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S") + suffix


def find_header(headers: Mapping[str, str], header_name: str) -> str | None:
    for name, value in headers.items():
        if name.lower() == header_name.lower():
            return value
    return None


class Provider:
    key: str
    display_name: str
    signature_header: str
    signature_summary: str
    secret_placeholder: str
    secret_environment_variables: tuple[str, ...]
    documentation_url: str
    signs_timestamp = False
    events: dict[str, EventTemplate]

    def serialize_payload(self, payload: dict) -> bytes:
        return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def build_headers(
        self, event_name: str, body: bytes, secret: str, *, timestamp: int, delivery_id: str
    ) -> dict[str, str]:
        raise NotImplementedError

    def create_delivery_id(self) -> str:
        raise NotImplementedError

    def verify_signature(
        self,
        headers: Mapping[str, str],
        body: bytes,
        secret: str,
        *,
        tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
        current_time: int | None = None,
    ) -> None:
        """Raises SignatureVerificationError unless the request is signed with the secret."""
        raise NotImplementedError

    def describe(self) -> dict:
        return {
            "key": self.key,
            "display_name": self.display_name,
            "signature_header": self.signature_header,
            "signature_summary": self.signature_summary,
            "secret_placeholder": self.secret_placeholder,
            "secret_environment_variable": self.secret_environment_variables[0],
            "documentation_url": self.documentation_url,
            "events": [
                {"name": event.name, "description": event.description} for event in self.events.values()
            ],
        }


def register_events(*event_templates: EventTemplate) -> dict[str, EventTemplate]:
    return {event_template.name: event_template for event_template in event_templates}
