#!/usr/bin/env python3
"""Validation helper for the nanobot-http-chat skill."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

DEFAULT_TIMEOUT = 30.0


def _load_dotenv() -> None:
    """Load environment variables from ~/.nanobot/.env when available."""
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
        key = key.strip()
        value = value.strip()
        if key and value and os.getenv(key) is None:
            os.environ[key] = value


_load_dotenv()


class CliUsageError(Exception):
    """Raised when the caller supplies invalid CLI input."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a reachable nanobot HTTP chat endpoint."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in ("show-config", "health", "models"):
        subparser = subparsers.add_parser(name)
        add_common_arguments(subparser)

    chat_parser = subparsers.add_parser("chat")
    add_common_arguments(chat_parser)
    chat_parser.add_argument("--session-id", required=True)
    chat_parser.add_argument("--message", required=True)
    chat_parser.add_argument("--model")

    return parser


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--base-url", dest="base_url")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)


def normalize_base_url(value: str | None) -> str | None:
    if not value:
        return None
    return value.rstrip("/") + "/"


def inspect_config(args: argparse.Namespace) -> dict[str, Any]:
    base_url = normalize_base_url(args.base_url or os.getenv("NANOBOT_BASE_URL"))
    missing: list[str] = []
    if not base_url:
        missing.append("base_url")
    return {
        "base_url": base_url,
        "ready": not missing,
        "missing": missing,
    }


def resolve_config(args: argparse.Namespace) -> dict[str, Any]:
    config = inspect_config(args)
    if not config["ready"]:
        raise CliUsageError("NANOBOT_BASE_URL or --base-url is required")
    return config


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=True))


def read_json_response(response: Any) -> Any:
    raw = response.read()
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def error_payload(kind: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"ok": False, "error": {"type": kind, "message": message}}
    payload.update(extra)
    return payload


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> tuple[int, Any]:
    url = urljoin(base_url, path.lstrip("/"))
    body: bytes | None = None
    headers = {
        "Accept": "application/json",
        "User-Agent": "nanobot-http-chat-skill/0.1",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload).encode("utf-8")

    request = Request(url, data=body, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        return getattr(response, "status", 200), read_json_response(response)


def fetch_default_model(base_url: str, timeout: float) -> str:
    _, payload = request_json(base_url, "/v1/models", timeout=timeout)
    _, model_id = parse_models_payload(payload)
    return model_id


def parse_models_payload(payload: Any) -> tuple[list[Any], str]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        raise CliUsageError("Model discovery returned no models")
    first_model = data[0]
    model_id = first_model.get("id") if isinstance(first_model, dict) else None
    if not isinstance(model_id, str) or not model_id:
        raise CliUsageError("Model discovery returned an invalid model id")
    return data, model_id


def command_show_config(args: argparse.Namespace) -> int:
    config = inspect_config(args)
    emit_json(
        {
            "ok": bool(config["ready"]),
            "base_url": config["base_url"],
            "ready": bool(config["ready"]),
            "missing": config["missing"],
        }
    )
    return 0 if config["ready"] else 1


def command_health(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    status, payload = request_json(config["base_url"], "/health", timeout=args.timeout)
    is_healthy = status == 200 and isinstance(payload, dict) and payload.get("status") == "ok"
    emit_json(
        {
            "ok": is_healthy,
            "base_url": config["base_url"],
            "status_code": status,
            "response": payload,
        }
    )
    return 0 if is_healthy else 1


def command_models(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    status, payload = request_json(config["base_url"], "/v1/models", timeout=args.timeout)
    models: list[Any] = []
    default_model = None
    is_ready = False
    if status == 200:
        try:
            models, default_model = parse_models_payload(payload)
        except CliUsageError:
            pass
        else:
            is_ready = True
    emit_json(
        {
            "ok": is_ready,
            "base_url": config["base_url"],
            "status_code": status,
            "models": models,
            "default_model": default_model,
        }
    )
    return 0 if is_ready else 1


def command_chat(args: argparse.Namespace) -> int:
    config = resolve_config(args)
    model = args.model or fetch_default_model(config["base_url"], args.timeout)
    payload = {
        "model": model,
        "session_id": args.session_id,
        "messages": [{"role": "user", "content": args.message}],
    }
    status, response = request_json(
        config["base_url"],
        "/v1/chat/completions",
        method="POST",
        payload=payload,
        timeout=args.timeout,
    )
    response_text = None
    if isinstance(response, dict):
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message", {})
            if isinstance(message, dict):
                response_text = message.get("content")
    emit_json(
        {
            "ok": status == 200 and isinstance(response_text, str),
            "base_url": config["base_url"],
            "status_code": status,
            "model": model,
            "session_id": args.session_id,
            "response_text": response_text,
            "response": response,
        }
    )
    return 0 if status == 200 and isinstance(response_text, str) else 1


def dispatch(args: argparse.Namespace) -> int:
    if args.command == "show-config":
        return command_show_config(args)
    if args.command == "health":
        return command_health(args)
    if args.command == "models":
        return command_models(args)
    if args.command == "chat":
        return command_chat(args)
    raise CliUsageError(f"Unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return dispatch(args)
    except CliUsageError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except HTTPError as exc:
        body = b""
        if exc.fp is not None:
            body = exc.fp.read()
        message = body.decode("utf-8", errors="replace").strip() or exc.reason
        emit_json(
            error_payload(
                "http_error",
                f"HTTP {exc.code} {exc.reason}",
                status_code=exc.code,
                details=message,
            )
        )
        return 1
    except URLError as exc:
        emit_json(error_payload("network_error", str(exc.reason)))
        return 1
    except json.JSONDecodeError as exc:
        emit_json(error_payload("invalid_json", f"Invalid JSON response: {exc.msg}"))
        return 1


if __name__ == "__main__":
    sys.exit(main())
