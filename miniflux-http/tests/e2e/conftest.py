"""pytest fixtures and CLI helpers for miniflux-http E2E tests."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "miniflux_http.py"


@pytest.fixture(scope="session")
def mf_url() -> str:
    return os.getenv("MINIFLUX_E2E_URL", "http://winnas:9090")


@pytest.fixture(scope="session")
def mf_api_key() -> str:
    key = os.getenv("MINIFLUX_E2E_API_KEY")
    if not key:
        pytest.skip("MINIFLUX_E2E_API_KEY not set")
    return key


class E2EClient:
    """Wraps miniflux_http.py request subcommand for E2E testing."""

    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.api_key = api_key

    def _run(
        self,
        method: str,
        path: str,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
        include_status: bool = True,
    ) -> tuple[int, Any, str]:
        """Run a request and return (exit_code, parsed_json_or_None, stderr)."""
        args = [
            sys.executable,
            str(SCRIPT),
            "request",
            "--base-url", self.base_url,
            "--api-key", self.api_key,
            "--method", method,
            "--path", path,
        ]
        if include_status:
            args.append("--include-status")
        if query:
            for k, v in query.items():
                args.extend(["--query", f"{k}={v}"])
        if body is not None:
            args.extend(["--body-json", json.dumps(body)])

        result = subprocess.run(
            args,
            text=True,
            capture_output=True,
            check=False,
        )

        # Parse stdout: may contain "HTTP 200\n" line followed by JSON
        stdout = result.stdout.strip()
        data: Any = None
        if stdout:
            # Strip status line if present
            lines = stdout.split("\n")
            json_start = 0
            if lines and lines[0].startswith("HTTP "):
                json_start = 1
            json_text = "\n".join(lines[json_start:])
            try:
                data = json.loads(json_text)
            except json.JSONDecodeError:
                data = json_text

        return result.returncode, data, result.stderr.strip()

    def get(self, path: str, query: dict[str, str] | None = None) -> tuple[int, Any, str]:
        return self._run("GET", path, query=query)

    def post(self, path: str, body: dict[str, Any]) -> tuple[int, Any, str]:
        return self._run("POST", path, body=body)

    def put(self, path: str, body: dict[str, Any]) -> tuple[int, Any, str]:
        return self._run("PUT", path, body=body)

    def delete(self, path: str) -> tuple[int, Any, str]:
        return self._run("DELETE", path)


@pytest.fixture(scope="session")
def cli(mf_url: str, mf_api_key: str) -> E2EClient:
    return E2EClient(base_url=mf_url, api_key=mf_api_key)
