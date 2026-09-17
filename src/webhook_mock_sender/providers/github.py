import hashlib
import hmac
import uuid
from collections.abc import Mapping

from webhook_mock_sender.providers.base import (
    DEFAULT_TOLERANCE_SECONDS,
    LOWERCASE_ALPHANUMERIC,
    EventTemplate,
    Provider,
    SignatureVerificationError,
    find_header,
    format_iso_timestamp,
    random_number,
    random_token,
    register_events,
)

REPOSITORY_ID = 186853002
REPOSITORY_FULL_NAME = "octo-org/hello-world"
REPOSITORY_URL = f"https://github.com/{REPOSITORY_FULL_NAME}"
REPOSITORY_API_URL = f"https://api.github.com/repos/{REPOSITORY_FULL_NAME}"
HOOK_ID = 109948940
HEXADECIMAL_DIGITS = "0123456789abcdef"


def build_user(login: str, user_id: int, *, user_type: str = "User") -> dict:
    return {
        "login": login,
        "id": user_id,
        "node_id": f"MDQ6VXNlcj{random_token(10)}",
        "avatar_url": f"https://avatars.githubusercontent.com/u/{user_id}?v=4",
        "url": f"https://api.github.com/users/{login}",
        "html_url": f"https://github.com/{login}",
        "type": user_type,
        "site_admin": False,
    }


def build_sender() -> dict:
    return build_user("octocat", 583231)


def build_repository(timestamp: int) -> dict:
    return {
        "id": REPOSITORY_ID,
        "node_id": "MDEwOlJlcG9zaXRvcnkxODY4NTMwMDI=",
        "name": "hello-world",
        "full_name": REPOSITORY_FULL_NAME,
        "private": False,
        "owner": build_user("octo-org", 6811672, user_type="Organization"),
        "html_url": REPOSITORY_URL,
        "description": "Mock repository for webhook tests",
        "fork": False,
        "url": REPOSITORY_API_URL,
        "created_at": format_iso_timestamp(timestamp - 90 * 24 * 60 * 60),
        "updated_at": format_iso_timestamp(timestamp),
        "pushed_at": format_iso_timestamp(timestamp),
        "clone_url": f"{REPOSITORY_URL}.git",
        "ssh_url": f"git@github.com:{REPOSITORY_FULL_NAME}.git",
        "stargazers_count": 42,
        "watchers_count": 42,
        "forks_count": 7,
        "open_issues_count": 3,
        "language": "Python",
        "visibility": "public",
        "default_branch": "main",
    }


def with_repository_context(timestamp: int, payload: dict) -> dict:
    return {**payload, "repository": build_repository(timestamp), "sender": build_sender()}


def build_ping(timestamp: int) -> dict:
    return with_repository_context(
        timestamp,
        {
            "zen": "Keep it logically awesome.",
            "hook_id": HOOK_ID,
            "hook": {
                "type": "Repository",
                "id": HOOK_ID,
                "name": "web",
                "active": True,
                "events": ["push", "pull_request"],
                "config": {"content_type": "json", "insecure_ssl": "0", "url": "https://example.com/webhooks/github"},
                "created_at": format_iso_timestamp(timestamp),
                "updated_at": format_iso_timestamp(timestamp),
            },
        },
    )


def build_push(timestamp: int) -> dict:
    previous_commit_sha = random_token(40, HEXADECIMAL_DIGITS)
    head_commit_sha = random_token(40, HEXADECIMAL_DIGITS)
    head_commit = {
        "id": head_commit_sha,
        "tree_id": random_token(40, HEXADECIMAL_DIGITS),
        "distinct": True,
        "message": "Fix retry backoff for failed deliveries",
        "timestamp": format_iso_timestamp(timestamp, suffix="+00:00"),
        "url": f"{REPOSITORY_URL}/commit/{head_commit_sha}",
        "author": {"name": "The Octocat", "email": "octocat@github.com", "username": "octocat"},
        "committer": {"name": "The Octocat", "email": "octocat@github.com", "username": "octocat"},
        "added": [],
        "removed": [],
        "modified": ["src/delivery/retry.py"],
    }
    return with_repository_context(
        timestamp,
        {
            "ref": "refs/heads/main",
            "before": previous_commit_sha,
            "after": head_commit_sha,
            "created": False,
            "deleted": False,
            "forced": False,
            "base_ref": None,
            "compare": f"{REPOSITORY_URL}/compare/{previous_commit_sha[:12]}...{head_commit_sha[:12]}",
            "commits": [head_commit],
            "head_commit": head_commit,
            "pusher": {"name": "octocat", "email": "octocat@github.com"},
        },
    )


def build_pull_request(timestamp: int) -> dict:
    pull_request_number = 27
    return with_repository_context(
        timestamp,
        {
            "action": "opened",
            "number": pull_request_number,
            "pull_request": {
                "id": random_number(10),
                "number": pull_request_number,
                "state": "open",
                "locked": False,
                "title": "Add signature verification to the webhook handler",
                "user": build_sender(),
                "body": "Verifies the HMAC signature before the event is processed.",
                "html_url": f"{REPOSITORY_URL}/pull/{pull_request_number}",
                "url": f"{REPOSITORY_API_URL}/pulls/{pull_request_number}",
                "created_at": format_iso_timestamp(timestamp),
                "updated_at": format_iso_timestamp(timestamp),
                "closed_at": None,
                "merged_at": None,
                "draft": False,
                "merged": False,
                "head": {"ref": "verify-signature", "sha": random_token(40, HEXADECIMAL_DIGITS)},
                "base": {"ref": "main", "sha": random_token(40, HEXADECIMAL_DIGITS)},
                "commits": 2,
                "additions": 84,
                "deletions": 6,
                "changed_files": 3,
            },
        },
    )


def build_issue(timestamp: int) -> dict:
    issue_number = 31
    return {
        "id": random_number(10),
        "number": issue_number,
        "title": "Webhook deliveries are processed twice",
        "user": build_sender(),
        "state": "open",
        "locked": False,
        "labels": [{"id": 208045946, "name": "bug", "color": "d73a4a", "default": True}],
        "assignees": [],
        "comments": 0,
        "html_url": f"{REPOSITORY_URL}/issues/{issue_number}",
        "url": f"{REPOSITORY_API_URL}/issues/{issue_number}",
        "created_at": format_iso_timestamp(timestamp),
        "updated_at": format_iso_timestamp(timestamp),
        "closed_at": None,
        "body": "The same delivery id shows up twice in the logs after a timeout.",
    }


def build_issues_opened(timestamp: int) -> dict:
    return with_repository_context(timestamp, {"action": "opened", "issue": build_issue(timestamp)})


def build_issue_comment(timestamp: int) -> dict:
    issue = build_issue(timestamp)
    comment_id = random_number(10)
    comment = {
        "id": comment_id,
        "html_url": f"{issue['html_url']}#issuecomment-{comment_id}",
        "user": build_sender(),
        "created_at": format_iso_timestamp(timestamp),
        "updated_at": format_iso_timestamp(timestamp),
        "body": "Reproduced: the handler is not idempotent.",
    }
    return with_repository_context(
        timestamp, {"action": "created", "issue": {**issue, "comments": 1}, "comment": comment}
    )


def build_release(timestamp: int) -> dict:
    return with_repository_context(
        timestamp,
        {
            "action": "published",
            "release": {
                "id": random_number(9),
                "tag_name": "v1.4.0",
                "target_commitish": "main",
                "name": "v1.4.0",
                "draft": False,
                "prerelease": False,
                "author": build_sender(),
                "created_at": format_iso_timestamp(timestamp),
                "published_at": format_iso_timestamp(timestamp),
                "html_url": f"{REPOSITORY_URL}/releases/tag/v1.4.0",
                "tarball_url": f"{REPOSITORY_API_URL}/tarball/v1.4.0",
                "zipball_url": f"{REPOSITORY_API_URL}/zipball/v1.4.0",
                "body": "Adds replay of failed deliveries.",
                "assets": [],
            },
        },
    )


def build_star(timestamp: int) -> dict:
    return with_repository_context(timestamp, {"action": "created", "starred_at": format_iso_timestamp(timestamp)})


def build_workflow_run(timestamp: int) -> dict:
    workflow_run_id = random_number(11)
    return with_repository_context(
        timestamp,
        {
            "action": "completed",
            "workflow_run": {
                "id": workflow_run_id,
                "name": "CI",
                "head_branch": "main",
                "head_sha": random_token(40, HEXADECIMAL_DIGITS),
                "path": ".github/workflows/ci.yml",
                "run_number": 128,
                "run_attempt": 1,
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "workflow_id": random_number(8),
                "html_url": f"{REPOSITORY_URL}/actions/runs/{workflow_run_id}",
                "created_at": format_iso_timestamp(timestamp - 192),
                "updated_at": format_iso_timestamp(timestamp),
                "run_started_at": format_iso_timestamp(timestamp - 192),
                "actor": build_sender(),
            },
            "workflow": {"id": random_number(8), "name": "CI", "path": ".github/workflows/ci.yml", "state": "active"},
        },
    )


class GitHubProvider(Provider):
    key = "github"
    display_name = "GitHub"
    signature_header = "X-Hub-Signature-256"
    signature_summary = "sha256=<hex HMAC-SHA256 of the raw body>"
    secret_placeholder = "the secret from the repository's webhook settings"
    secret_environment_variables = ("GITHUB_WEBHOOK_SECRET",)
    documentation_url = "https://docs.github.com/en/webhooks/using-webhooks/validating-webhook-deliveries"
    events = register_events(
        EventTemplate("ping", "Sent once when a webhook is created.", build_ping),
        EventTemplate("push", "A commit was pushed to main.", build_push),
        EventTemplate("pull_request", "A pull request was opened.", build_pull_request),
        EventTemplate("issues", "An issue was opened.", build_issues_opened),
        EventTemplate("issue_comment", "A comment was added to an issue.", build_issue_comment),
        EventTemplate("release", "A release was published.", build_release),
        EventTemplate("star", "Someone starred the repository.", build_star),
        EventTemplate("workflow_run", "A GitHub Actions workflow run finished.", build_workflow_run),
    )

    def create_delivery_id(self) -> str:
        return str(uuid.uuid4())

    def build_headers(
        self, event_name: str, body: bytes, secret: str, *, timestamp: int, delivery_id: str
    ) -> dict[str, str]:
        secret_bytes = secret.encode("utf-8")
        return {
            "Content-Type": "application/json",
            "User-Agent": f"GitHub-Hookshot/{random_token(7, LOWERCASE_ALPHANUMERIC)}",
            "X-GitHub-Delivery": delivery_id,
            "X-GitHub-Event": event_name,
            "X-GitHub-Hook-ID": str(HOOK_ID),
            "X-GitHub-Hook-Installation-Target-ID": str(REPOSITORY_ID),
            "X-GitHub-Hook-Installation-Target-Type": "repository",
            "X-Hub-Signature": f"sha1={hmac.new(secret_bytes, body, hashlib.sha1).hexdigest()}",
            "X-Hub-Signature-256": f"sha256={hmac.new(secret_bytes, body, hashlib.sha256).hexdigest()}",
        }

    def verify_signature(
        self,
        headers: Mapping[str, str],
        body: bytes,
        secret: str,
        *,
        tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
        current_time: int | None = None,
    ) -> None:
        received_signature = find_header(headers, self.signature_header)
        if not received_signature:
            raise SignatureVerificationError("X-Hub-Signature-256 header is missing")
        expected_signature = f"sha256={hmac.new(secret.encode('utf-8'), body, hashlib.sha256).hexdigest()}"
        if not hmac.compare_digest(expected_signature, received_signature):
            raise SignatureVerificationError("X-Hub-Signature-256 does not match the body and the secret")
