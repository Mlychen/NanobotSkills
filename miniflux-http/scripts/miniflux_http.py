#!/usr/bin/env python3
"""Authenticated Miniflux HTTP requester for the miniflux-http skill."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


def _load_dotenv():
    """Load environment variables from ~/.nanobot/.env if available."""
    dotenv_path = Path.home() / ".nanobot" / ".env"
    if not dotenv_path.exists():
        return
    try:
        content = dotenv_path.read_text(encoding="utf-8")
    except OSError:
        return
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if key and value and os.getenv(key) is None:
            os.environ[key] = value


_load_dotenv()


class CliUsageError(Exception):
    """Raised when the user provides invalid CLI input."""


def write_text(stream: object, text: str) -> None:
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        stream.write(text.encode(encoding, errors="replace").decode(encoding))


def write_bytes_to_stdout(data: bytes) -> None:
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(data)
        return
    write_text(sys.stdout, data.decode("utf-8", errors="replace"))


def flush_stream(stream: object) -> None:
    flush = getattr(stream, "flush", None)
    if callable(flush):
        flush()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send authenticated Miniflux HTTP requests."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("request", "show-config", "mark-read"):
        subparser = subparsers.add_parser(name)
        add_common_arguments(subparser)
        if name == "request":
            add_request_arguments(subparser)
        elif name == "mark-read":
            add_mark_read_arguments(subparser)

    return parser


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", dest="base_url")
    parser.add_argument("--api-key", dest="api_key")
    parser.add_argument("--username")
    parser.add_argument("--password")
    parser.add_argument("--timeout", type=float, default=30.0)


def add_request_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--method", default="GET")
    parser.add_argument("--path", required=True)
    parser.add_argument(
        "--query",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Repeatable query string item.",
    )
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Repeatable header override.",
    )
    parser.add_argument("--body-json")
    parser.add_argument("--body-file")
    parser.add_argument("--raw", action="store_true")
    parser.add_argument("--include-status", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--title-only",
        action="store_true",
        help="Only output entry titles and metadata, strip body content.",
    )


def add_mark_read_arguments(parser: argparse.ArgumentParser) -> None:
    scope_group = parser.add_mutually_exclusive_group(required=True)
    scope_group.add_argument(
        "--all",
        action="store_true",
        help="Mark all unread entries for the authenticated user as read.",
    )
    scope_group.add_argument(
        "--category-id",
        type=int,
        help="Mark all unread entries in the given category as read.",
    )
    scope_group.add_argument(
        "--category",
        help="Mark all unread entries in the named category as read.",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        help="Override the Miniflux user ID instead of resolving it from /v1/me.",
    )
    parser.add_argument("--include-status", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


def inspect_config(args: argparse.Namespace) -> dict[str, object]:
    base_url = args.base_url or os.getenv("MINIFLUX_URL")
    api_key = args.api_key or os.getenv("MINIFLUX_API_KEY")
    username = args.username or os.getenv("MINIFLUX_USERNAME")
    password = args.password or os.getenv("MINIFLUX_PASSWORD")

    if api_key:
        auth_mode = "api_key"
    elif username and password:
        auth_mode = "basic"
    else:
        auth_mode = None

    missing: list[str] = []
    if not base_url:
        missing.append("base_url")
    if not api_key and not username and not password:
        missing.append("api_key_or_basic_auth")
    elif not api_key and username and not password:
        missing.append("password")
    elif not api_key and password and not username:
        missing.append("api_key_or_basic_auth")

    return {
        "base_url": str(base_url).rstrip("/") + "/" if base_url else None,
        "api_key": api_key,
        "username": username,
        "password": password,
        "auth_mode": auth_mode,
        "ready": not missing,
        "missing": missing,
    }


def resolve_request_config(args: argparse.Namespace) -> dict[str, object]:
    config = inspect_config(args)
    if not config["base_url"]:
        raise CliUsageError("MINIFLUX_URL or --base-url is required")
    if config["missing"]:
        raise CliUsageError(
            "Provide MINIFLUX_API_KEY or both MINIFLUX_USERNAME and MINIFLUX_PASSWORD"
        )
    return config


def parse_pairs(items: Iterable[str]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in items:
        if "=" not in item:
            raise CliUsageError(f"Expected KEY=VALUE pair, got: {item}")
        key, value = item.split("=", 1)
        parsed[key] = value
    return parsed


def build_headers(config: dict[str, object], header_items: Iterable[str]) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "miniflux-http-skill/0.1",
    }
    headers.update(parse_pairs(header_items))

    if config["auth_mode"] == "api_key":
        headers.setdefault("X-Auth-Token", str(config["api_key"]))
        return headers

    token = base64.b64encode(
        f"{config['username']}:{config['password']}".encode("utf-8")
    ).decode("ascii")
    headers.setdefault("Authorization", f"Basic {token}")
    return headers


def build_url(base_url: str, path: str, query_items: Iterable[str]) -> str:
    normalized_path = path[1:] if path.startswith("/") else path
    url = urljoin(base_url, normalized_path)
    query = parse_pairs(query_items)
    if query:
        return f"{url}?{urlencode(query)}"
    return url


def load_body(args: argparse.Namespace, headers: dict[str, str]) -> bytes | None:
    if args.body_json and args.body_file:
        raise CliUsageError("Use either --body-json or --body-file, not both")

    if args.body_json:
        headers.setdefault("Content-Type", "application/json")
        try:
            parsed = json.loads(args.body_json)
        except json.JSONDecodeError as exc:
            raise CliUsageError(f"Invalid JSON for --body-json: {exc.msg}") from exc
        return json.dumps(parsed).encode("utf-8")

    if args.body_file:
        headers.setdefault("Content-Type", "application/json")
        try:
            return Path(args.body_file).read_bytes()
        except FileNotFoundError as exc:
            raise CliUsageError(f"Body file not found: {args.body_file}") from exc

    return None


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted = dict(headers)
    if "X-Auth-Token" in redacted:
        redacted["X-Auth-Token"] = "***"
    if "Authorization" in redacted:
        redacted["Authorization"] = "Basic ***"
    return redacted


def preview_request(method: str, url: str, headers: dict[str, str], body: bytes | None) -> int:
    preview = {
        "method": method.upper(),
        "url": url,
        "headers": redact_headers(headers),
        "has_body": body is not None,
    }
    print(json.dumps(preview, indent=2, ensure_ascii=True))
    if body is not None:
        write_text(sys.stdout, body.decode("utf-8", errors="replace"))
        if not body.endswith(b"\n"):
            write_text(sys.stdout, "\n")
    return 0


def execute_request(
    *,
    url: str,
    method: str,
    headers: dict[str, str],
    body: bytes | None,
    timeout: float,
    raw: bool = False,
    include_status: bool = False,
    dry_run: bool = False,
    title_only: bool = False,
) -> int:
    if dry_run:
        return preview_request(method, url, headers, body)

    request = Request(
        url=url,
        data=body,
        headers=headers,
        method=method.upper(),
    )

    try:
        with urlopen(request, timeout=timeout) as response:
            return render_response(
                response.read(),
                raw=raw,
                include_status=include_status,
                status=response.status,
                title_only=title_only,
            )
    except HTTPError as exc:
        error_body = exc.read()
        status_line = f"HTTP {exc.code}"
        if exc.reason:
            status_line = f"{status_line} {exc.reason}"
        write_text(sys.stderr, f"{status_line}\n")
        if error_body:
            write_text(sys.stderr, error_body.decode("utf-8", errors="replace"))
        else:
            write_text(sys.stderr, "Request failed with an empty error response.\n")
        if error_body and not error_body.endswith(b"\n"):
            write_text(sys.stderr, "\n")
        return 1
    except URLError as exc:
        write_text(sys.stderr, f"Request failed: {exc}\n")
        return 1


def resolve_current_user_id(
    config: dict[str, object], headers: dict[str, str], timeout: float
) -> int:
    url = build_url(str(config["base_url"]), "/v1/me", [])
    request = Request(
        url=url,
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise CliUsageError(f"Unable to resolve current user from /v1/me: HTTP {exc.code}") from exc
    except URLError as exc:
        raise CliUsageError(f"Unable to resolve current user from /v1/me: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CliUsageError("Unable to resolve current user from /v1/me: invalid JSON response") from exc

    user_id = payload.get("id")
    if not isinstance(user_id, int):
        raise CliUsageError("Unable to resolve current user from /v1/me: missing integer id")
    return user_id


def resolve_category_id(
    config: dict[str, object], headers: dict[str, str], timeout: float, category_name: str
) -> int:
    url = build_url(str(config["base_url"]), "/v1/categories", [])
    request = Request(
        url=url,
        headers=headers,
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise CliUsageError(f"Unable to resolve category from /v1/categories: HTTP {exc.code}") from exc
    except URLError as exc:
        raise CliUsageError(f"Unable to resolve category from /v1/categories: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CliUsageError("Unable to resolve category from /v1/categories: invalid JSON response") from exc

    if not isinstance(payload, list):
        raise CliUsageError("Unable to resolve category from /v1/categories: expected category list")

    exact_matches = [
        item for item in payload
        if isinstance(item, dict) and item.get("title") == category_name and isinstance(item.get("id"), int)
    ]
    if len(exact_matches) == 1:
        return int(exact_matches[0]["id"])
    if len(exact_matches) > 1:
        raise CliUsageError(f"Category name is ambiguous: {category_name}")

    normalized = category_name.casefold()
    folded_matches = [
        item for item in payload
        if (
            isinstance(item, dict)
            and isinstance(item.get("title"), str)
            and item["title"].casefold() == normalized
            and isinstance(item.get("id"), int)
        )
    ]
    if len(folded_matches) == 1:
        return int(folded_matches[0]["id"])
    if len(folded_matches) > 1:
        raise CliUsageError(f"Category name is ambiguous: {category_name}")
    raise CliUsageError(f"Category not found: {category_name}")


def command_show_config(args: argparse.Namespace) -> int:
    config = inspect_config(args)
    payload = {
        "base_url": config["base_url"],
        "auth_mode": config["auth_mode"],
        "has_api_key": bool(config["api_key"]),
        "has_username": bool(config["username"]),
        "has_password": bool(config["password"]),
        "ready": bool(config["ready"]),
        "missing": config["missing"],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=True))
    return 0 if config["ready"] else 1


BODY_FIELDS = frozenset(
    (
        "content",
        "summary",
        "hash",
    )
)


def strip_body(entry: dict) -> dict:
    """Remove body-level content fields while preserving metadata and nested objects."""
    return {k: v for k, v in entry.items() if k not in BODY_FIELDS}


def render_response(
    data: bytes,
    raw: bool,
    include_status: bool,
    status: int,
    *,
    title_only: bool = False,
) -> int:
    if include_status:
        write_text(sys.stdout, f"HTTP {status}\n")
        flush_stream(sys.stdout)

    if raw:
        write_bytes_to_stdout(data)
        if data and not data.endswith(b"\n"):
            write_bytes_to_stdout(b"\n")
        flush_stream(sys.stdout)
        return 0

    try:
        parsed = json.loads(data.decode("utf-8"))
    except json.JSONDecodeError:
        write_text(sys.stdout, data.decode("utf-8", errors="replace"))
        if data and not data.endswith(b"\n"):
            write_text(sys.stdout, "\n")
        return 0

    if title_only and isinstance(parsed, dict):
        if "entries" in parsed:
            parsed["entries"] = [strip_body(e) if isinstance(e, dict) else e for e in parsed["entries"]]
        else:
            parsed = strip_body(parsed)

    print(json.dumps(parsed, indent=2, ensure_ascii=True))
    return 0


def command_request(args: argparse.Namespace) -> int:
    config = resolve_request_config(args)
    headers = build_headers(config, args.header)
    body = load_body(args, headers)
    url = build_url(str(config["base_url"]), args.path, args.query)

    return execute_request(
        url=url,
        headers=headers,
        body=body,
        method=args.method,
        timeout=args.timeout,
        raw=args.raw,
        include_status=args.include_status,
        dry_run=args.dry_run,
        title_only=args.title_only,
    )


def command_mark_read(args: argparse.Namespace) -> int:
    config = resolve_request_config(args)
    headers = build_headers(config, [])
    if args.category_id is not None:
        url = build_url(
            str(config["base_url"]),
            f"/v1/categories/{args.category_id}/mark-all-as-read",
            [],
        )
    elif args.category is not None:
        category_id = resolve_category_id(config, headers, args.timeout, args.category)
        url = build_url(
            str(config["base_url"]),
            f"/v1/categories/{category_id}/mark-all-as-read",
            [],
        )
    else:
        user_id = args.user_id if args.user_id is not None else resolve_current_user_id(
            config, headers, args.timeout
        )
        url = build_url(
            str(config["base_url"]), f"/v1/users/{user_id}/mark-all-as-read", []
        )
    return execute_request(
        url=url,
        method="PUT",
        headers=headers,
        body=None,
        timeout=args.timeout,
        include_status=args.include_status,
        dry_run=args.dry_run,
    )


def main() -> int:
    parser = build_parser()
    try:
        args = parser.parse_args()

        if args.command == "show-config":
            return command_show_config(args)
        if args.command == "request":
            return command_request(args)
        if args.command == "mark-read":
            return command_mark_read(args)

        parser.error(f"Unknown command: {args.command}")
        return 2
    except CliUsageError as exc:
        write_text(sys.stderr, f"{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
