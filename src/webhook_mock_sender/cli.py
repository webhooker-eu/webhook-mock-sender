import argparse
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from webhook_mock_sender.mock_request import InvalidRequestError, MockRequest, build_curl_command, build_mock_request
from webhook_mock_sender.providers import PROVIDERS, Provider, UnknownProviderError, get_provider
from webhook_mock_sender.sender import DeliveryResult, WebhookSender, validate_target_url

PROGRAM_NAME = "webhook-mock-sender"
TARGET_URL_ENVIRONMENT_VARIABLE = "WEBHOOK_MOCK_URL"
SECRET_ENVIRONMENT_VARIABLE = "WEBHOOK_MOCK_SECRET"
TARGET_URL_PLACEHOLDER = "$WEBHOOK_MOCK_URL"
MAX_REPEAT_COUNT = 100
EXIT_OK = 0
EXIT_DELIVERY_FAILED = 1
EXIT_INVALID_INPUT = 2


class InvalidInputError(Exception):
    pass


class Terminal:
    def __init__(self, stream):
        self._stream = stream
        self._use_color = stream.isatty() and "NO_COLOR" not in os.environ

    def _paint(self, text: str, color_code: str) -> str:
        return f"\033[{color_code}m{text}\033[0m" if self._use_color else text

    def success(self, text: str) -> None:
        print(self._paint(f"✓ {text}", "32"), file=self._stream)

    def failure(self, text: str) -> None:
        print(self._paint(f"✗ {text}", "31"), file=self._stream)

    def heading(self, text: str) -> None:
        print(self._paint(text, "1"), file=self._stream, flush=True)

    def detail(self, label: str, value) -> None:
        print(f"  {self._paint(label.ljust(11), '2')} {value}", file=self._stream)

    def line(self, text: str = "") -> None:
        print(text, file=self._stream)


def get_program_version() -> str:
    try:
        return version(PROGRAM_NAME)
    except PackageNotFoundError:
        return "0.0.0+local"


def resolve_provider(provider_key: str) -> Provider:
    try:
        return get_provider(provider_key)
    except UnknownProviderError as provider_error:
        raise InvalidInputError(str(provider_error)) from None


def resolve_secret(arguments: argparse.Namespace, provider: Provider) -> str:
    environment_variables = (*provider.secret_environment_variables, SECRET_ENVIRONMENT_VARIABLE)
    environment_secrets = [os.environ.get(variable_name) for variable_name in environment_variables]
    secret = arguments.secret or next((value for value in environment_secrets if value), None)
    if not secret:
        raise InvalidInputError(
            f"No signing secret: pass --secret or set {' or '.join(environment_variables)}. "
            "Use the same value your endpoint verifies against."
        )
    return secret


def parse_header_argument(header_argument: str) -> tuple[str, str]:
    header_name, separator, header_value = header_argument.partition(":")
    if not separator or not header_name.strip():
        raise InvalidInputError(f"Invalid --header {header_argument!r}: expected 'Name: value'")
    return header_name.strip(), header_value.strip()


def load_body_file(payload_file: str) -> bytes:
    try:
        body = sys.stdin.buffer.read() if payload_file == "-" else Path(payload_file).read_bytes()
    except OSError as os_error:
        raise InvalidInputError(f"Cannot read payload file: {os_error}") from None
    try:
        json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as json_error:
        raise InvalidInputError(f"Payload file is not valid JSON: {json_error}") from None
    return body


def print_request(target_url: str, mock_request: MockRequest) -> None:
    terminal = Terminal(sys.stdout)
    terminal.heading(f"POST {target_url}")
    for header_name, header_value in mock_request.headers.items():
        terminal.line(f"{header_name}: {header_value}")
    terminal.line()
    terminal.line(mock_request.body.decode("utf-8"))


def report_delivery(result: DeliveryResult, *, is_expected: bool, expected_status: int | None, position: str) -> None:
    terminal = Terminal(sys.stdout if is_expected else sys.stderr)
    status_text = f"HTTP {result.status_code}" if result.status_code else "no response"
    if is_expected:
        terminal.success(f"Delivery{position}: {status_text} in {result.elapsed_ms} ms")
    elif expected_status is not None and result.status_code is not None:
        terminal.failure(f"Delivery{position}: expected HTTP {expected_status}, got {status_text}")
    else:
        terminal.failure(f"Delivery{position}: {result.error or status_text}")
    if result.response_body:
        terminal.detail("response", result.response_body.strip().replace("\n", "\n" + " " * 14))
    if result.hint:
        terminal.detail("hint", result.hint)


def run_send(arguments: argparse.Namespace) -> int:
    provider = resolve_provider(arguments.provider)
    is_preview = arguments.dry_run or arguments.curl
    target_url = arguments.url or os.environ.get(TARGET_URL_ENVIRONMENT_VARIABLE)
    if not target_url and not is_preview:
        raise InvalidInputError(f"No endpoint URL: pass it as an argument or set {TARGET_URL_ENVIRONMENT_VARIABLE}")
    if not 1 <= arguments.repeat <= MAX_REPEAT_COUNT:
        raise InvalidInputError(f"--repeat must be between 1 and {MAX_REPEAT_COUNT}")

    try:
        if target_url:
            target_url = validate_target_url(target_url)
        mock_request = build_mock_request(
            provider,
            arguments.event,
            resolve_secret(arguments, provider),
            body=load_body_file(arguments.payload_file) if arguments.payload_file else None,
            overrides=arguments.set,
            timestamp_offset_seconds=arguments.timestamp_offset,
            use_invalid_signature=arguments.invalid_signature,
            extra_headers=dict(parse_header_argument(header_argument) for header_argument in arguments.header),
        )
    except InvalidRequestError as request_error:
        raise InvalidInputError(str(request_error)) from None

    if arguments.curl:
        print(build_curl_command(target_url or TARGET_URL_PLACEHOLDER, mock_request))
        return EXIT_OK
    if arguments.dry_run:
        if arguments.json:
            print(json.dumps(mock_request.to_dict(), indent=2, ensure_ascii=False))
        else:
            print_request(target_url or TARGET_URL_PLACEHOLDER, mock_request)
        return EXIT_OK

    results: list[DeliveryResult] = []
    with WebhookSender(timeout_seconds=arguments.timeout, verify_tls=not arguments.insecure) as sender:
        for _ in range(arguments.repeat):
            results.append(sender.send(target_url, mock_request))

    def is_expected(result: DeliveryResult) -> bool:
        return result.status_code == arguments.expect if arguments.expect is not None else result.ok

    if arguments.json:
        output = {"request": mock_request.to_dict(), "deliveries": [result.to_dict() for result in results]}
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        signature_note = "" if mock_request.is_signature_valid else ", signature wrong on purpose"
        if mock_request.is_timestamp_stale:
            signature_note += ", signed time too old on purpose"
        Terminal(sys.stdout).heading(f"{provider.display_name} {arguments.event} → {target_url}{signature_note}")
        for delivery_number, result in enumerate(results, start=1):
            position = f" {delivery_number}/{len(results)}" if len(results) > 1 else ""
            report_delivery(result, is_expected=is_expected(result), expected_status=arguments.expect, position=position)
    return EXIT_OK if all(is_expected(result) for result in results) else EXIT_DELIVERY_FAILED


def run_list(arguments: argparse.Namespace) -> int:
    providers = [resolve_provider(arguments.provider)] if arguments.provider else list(PROVIDERS.values())
    if arguments.json:
        print(json.dumps([provider.describe() for provider in providers], indent=2, ensure_ascii=False))
        return EXIT_OK
    terminal = Terminal(sys.stdout)
    for provider in providers:
        terminal.heading(f"{provider.key}  ({provider.signature_header}: {provider.signature_summary})")
        name_width = max(len(event_name) for event_name in provider.events)
        for event in provider.events.values():
            terminal.line(f"  {event.name.ljust(name_width)}  {event.description}")
        terminal.line()
    return EXIT_OK


def run_serve(arguments: argparse.Namespace) -> int:
    import uvicorn

    from webhook_mock_sender.web import create_app

    is_wildcard_host = arguments.host in ("0.0.0.0", "::")
    browser_host = "127.0.0.1" if is_wildcard_host else arguments.host
    if ":" in browser_host:
        browser_host = f"[{browser_host}]"
    page_url = f"http://{browser_host}:{arguments.port}"
    if arguments.open:
        import threading
        import webbrowser

        threading.Timer(1.0, webbrowser.open, args=(page_url,)).start()
    print(f"Webhook Mock Sender is running at {page_url} (Ctrl+C to stop)")
    uvicorn.run(create_app(), host=arguments.host, port=arguments.port, log_level="warning")
    return EXIT_OK


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description="Send fake Stripe, GitHub and Shopify webhook events with a valid signature to your endpoint.",
        epilog="Created by Webhooker — https://webhooker.eu/",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {get_program_version()}")
    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    send_parser = subparsers.add_parser(
        "send",
        help="send a signed mock event to an endpoint",
        description="Build a mock event, sign it the way the provider does and POST it to your endpoint.",
    )
    send_parser.add_argument("provider", help=f"one of: {', '.join(PROVIDERS)}")
    send_parser.add_argument("event", help="event name, see the 'list' command (for example payment_intent.succeeded)")
    send_parser.add_argument("url", nargs="?", help=f"your endpoint (default: ${TARGET_URL_ENVIRONMENT_VARIABLE})")
    send_parser.add_argument(
        "--secret", help=f"signing secret (default: the provider's variable, then ${SECRET_ENVIRONMENT_VARIABLE})"
    )

    payload_group = send_parser.add_argument_group("payload")
    payload_group.add_argument(
        "--set", action="append", default=[], metavar="PATH=VALUE", help="change a field, like data.object.amount=5000"
    )
    payload_group.add_argument(
        "--payload-file", metavar="PATH", help="send this JSON body instead of a template ('-' reads stdin)"
    )
    payload_group.add_argument(
        "--header", action="append", default=[], metavar="'NAME: VALUE'", help="add or replace a header (repeatable)"
    )

    scenario_group = send_parser.add_argument_group("failure scenarios")
    scenario_group.add_argument(
        "--invalid-signature", action="store_true", help="sign with a wrong secret: your endpoint should refuse it"
    )
    scenario_group.add_argument(
        "--timestamp-offset", type=int, default=0, metavar="SECONDS", help="shift the signed time, -600 imitates a replay"
    )
    scenario_group.add_argument(
        "--repeat", type=int, default=1, metavar="COUNT", help="send the same delivery COUNT times to test idempotency"
    )
    scenario_group.add_argument(
        "--expect", type=int, metavar="STATUS", help="succeed only if the endpoint answers with this status code"
    )

    output_group = send_parser.add_argument_group("output")
    output_group.add_argument("--dry-run", action="store_true", help="print the signed request and exit without sending")
    output_group.add_argument("--curl", action="store_true", help="print the request as a curl command and exit")
    output_group.add_argument("--json", action="store_true", help="print the result as JSON")
    output_group.add_argument("--timeout", type=float, default=15.0, metavar="SECONDS", help="request timeout (default: 15)")
    output_group.add_argument("--insecure", action="store_true", help="do not verify the endpoint's TLS certificate")
    send_parser.set_defaults(handler=run_send)

    list_parser = subparsers.add_parser(
        "list", help="list the mock events", description="List the providers and the mock events of each."
    )
    list_parser.add_argument("provider", nargs="?", help="show one provider only")
    list_parser.add_argument("--json", action="store_true", help="print the catalog as JSON")
    list_parser.set_defaults(handler=run_list)

    serve_parser = subparsers.add_parser(
        "serve",
        help="start the local web form",
        description="Start the web form: pick an event, edit the payload, see the signed request and send it.",
    )
    serve_parser.add_argument("--host", default="127.0.0.1", help="address to listen on (default: 127.0.0.1)")
    serve_parser.add_argument("--port", type=int, default=8080, help="port to listen on (default: 8080)")
    serve_parser.add_argument("--open", action="store_true", help="open the form in the default browser")
    serve_parser.set_defaults(handler=run_serve)
    return parser


def main(argument_list: list[str] | None = None) -> int:
    arguments = build_argument_parser().parse_args(argument_list)
    try:
        return arguments.handler(arguments)
    except InvalidInputError as input_error:
        Terminal(sys.stderr).failure(str(input_error))
        return EXIT_INVALID_INPUT
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
