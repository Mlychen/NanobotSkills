import argparse
import importlib.util
import json
import os
import subprocess
import sys
import unittest
from io import BytesIO
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "miniflux_http.py"


def load_module():
    spec = importlib.util.spec_from_file_location("miniflux_http_script", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


class DummyHTTPResponse:
    def __init__(self, body: bytes):
        self._body = body
        self.status = 200

    def read(self) -> bytes:
        return self._body

    def close(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


class FakeStdout:
    def __init__(self):
        self.buffer = BytesIO()
        self.encoding = "ascii"
        self.text_writes: list[str] = []

    def write(self, text: str) -> int:
        self.text_writes.append(text)
        return len(text)


class MinifluxHttpCliTests(unittest.TestCase):
    def run_cli(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        cli_env = os.environ.copy()
        for key in (
            "MINIFLUX_URL",
            "MINIFLUX_API_KEY",
            "MINIFLUX_USERNAME",
            "MINIFLUX_PASSWORD",
        ):
            cli_env.pop(key, None)
        if env:
            cli_env.update(env)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            text=True,
            capture_output=True,
            env=cli_env,
            check=False,
        )

    def test_show_config_without_any_configuration(self) -> None:
        result = self.run_cli("show-config")
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["missing"], ["base_url", "api_key_or_basic_auth"])

    def test_show_config_with_only_base_url(self) -> None:
        result = self.run_cli(
            "show-config",
            env={"MINIFLUX_URL": "http://example.test"},
        )
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(payload["ready"])
        self.assertEqual(payload["auth_mode"], None)
        self.assertEqual(payload["missing"], ["api_key_or_basic_auth"])

    def test_show_config_with_api_key_is_ready(self) -> None:
        result = self.run_cli(
            "show-config",
            env={
                "MINIFLUX_URL": "http://example.test",
                "MINIFLUX_API_KEY": "secret",
            },
        )
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["auth_mode"], "api_key")
        self.assertEqual(payload["missing"], [])

    def test_show_config_partial_basic_auth_not_ready(self) -> None:
        result = self.run_cli(
            "show-config",
            env={
                "MINIFLUX_URL": "http://example.test",
                "MINIFLUX_USERNAME": "admin",
            },
        )
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 1)
        self.assertFalse(payload["ready"])
        self.assertIsNone(payload["auth_mode"])
        self.assertIn("password", payload["missing"])

    def test_show_config_full_basic_auth_is_ready(self) -> None:
        result = self.run_cli(
            "show-config",
            env={
                "MINIFLUX_URL": "http://example.test",
                "MINIFLUX_USERNAME": "admin",
                "MINIFLUX_PASSWORD": "pass",
            },
        )
        payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0)
        self.assertTrue(payload["ready"])
        self.assertEqual(payload["auth_mode"], "basic")
        self.assertEqual(payload["missing"], [])

    def test_invalid_body_json_returns_usage_error(self) -> None:
        result = self.run_cli(
            "request",
            "--base-url",
            "http://example.test",
            "--api-key",
            "secret",
            "--path",
            "/v1/me",
            "--body-json",
            "{bad json}",
            "--dry-run",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("Invalid JSON for --body-json", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_missing_body_file_returns_usage_error(self) -> None:
        result = self.run_cli(
            "request",
            "--base-url",
            "http://example.test",
            "--api-key",
            "secret",
            "--path",
            "/v1/me",
            "--body-file",
            "missing.json",
            "--dry-run",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("Body file not found: missing.json", result.stderr)
        self.assertNotIn("Traceback", result.stderr)


class MinifluxHttpRequestErrorTests(unittest.TestCase):
    def make_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            base_url="http://example.test",
            api_key="secret",
            username=None,
            password=None,
            timeout=30.0,
            method="GET",
            path="/v1/test",
            query=[],
            header=[],
            body_json=None,
            body_file=None,
            raw=False,
            include_status=False,
            dry_run=False,
            command="request",
        )

    def run_request_with_http_error(self, error: HTTPError) -> tuple[int, str]:
        args = self.make_args()
        stderr = StringIO()
        with patch.object(MODULE, "urlopen", side_effect=error):
            with patch.object(sys, "stderr", stderr):
                code = MODULE.command_request(args)
        return code, stderr.getvalue()

    def test_http_error_with_empty_body_prints_status(self) -> None:
        error = HTTPError(
            url="http://example.test/v1/test",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=DummyHTTPResponse(b""),
        )

        code, stderr = self.run_request_with_http_error(error)

        self.assertEqual(code, 1)
        self.assertIn("HTTP 500 Internal Server Error", stderr)
        self.assertIn("empty error response", stderr)

    def test_http_error_with_json_body_prints_status_and_body(self) -> None:
        body = b'{"error_message":"access unauthorized"}'
        error = HTTPError(
            url="http://example.test/v1/test",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=DummyHTTPResponse(body),
        )

        code, stderr = self.run_request_with_http_error(error)

        self.assertEqual(code, 1)
        self.assertIn("HTTP 401 Unauthorized", stderr)
        self.assertIn('{"error_message":"access unauthorized"}', stderr)

    def test_render_response_raw_uses_stdout_buffer(self) -> None:
        stdout = FakeStdout()
        payload = "<?xml version=\"1.0\"?><opml>֎</opml>".encode("utf-8")

        with patch.object(sys, "stdout", stdout):
            code = MODULE.render_response(payload, raw=True, include_status=False, status=200)

        self.assertEqual(code, 0)
        self.assertEqual(stdout.buffer.getvalue(), payload + b"\n")
        self.assertEqual(stdout.text_writes, [])


class MinifluxHttpHelperTests(unittest.TestCase):
    def test_parse_pairs_valid(self) -> None:
        result = MODULE.parse_pairs(["status=unread", "limit=10"])
        self.assertEqual(result, {"status": "unread", "limit": "10"})

    def test_parse_pairs_invalid(self) -> None:
        with self.assertRaises(MODULE.CliUsageError):
            MODULE.parse_pairs(["badpair"])

    def test_build_url_basic_path(self) -> None:
        url = MODULE.build_url("http://host:9090/", "/v1/me", [])
        self.assertEqual(url, "http://host:9090/v1/me")

    def test_build_url_with_query(self) -> None:
        url = MODULE.build_url("http://host:9090/", "/v1/entries", ["status=unread", "limit=5"])
        self.assertIn("status=unread", url)
        self.assertIn("limit=5", url)
        self.assertTrue(url.startswith("http://host:9090/v1/entries?"))

    def test_build_headers_api_key(self) -> None:
        config = {
            "auth_mode": "api_key",
            "api_key": "secret-key",
            "username": None,
            "password": None,
        }
        headers = MODULE.build_headers(config, [])
        self.assertEqual(headers["X-Auth-Token"], "secret-key")
        self.assertNotIn("Authorization", headers)

    def test_build_headers_basic_auth(self) -> None:
        config = {
            "auth_mode": "basic",
            "api_key": None,
            "username": "admin",
            "password": "pass",
        }
        headers = MODULE.build_headers(config, [])
        self.assertIn("Authorization", headers)
        self.assertTrue(headers["Authorization"].startswith("Basic "))

    def test_build_headers_custom_header_override(self) -> None:
        config = {
            "auth_mode": "api_key",
            "api_key": "secret-key",
            "username": None,
            "password": None,
        }
        headers = MODULE.build_headers(config, ["Accept=application/xml"])
        self.assertEqual(headers["Accept"], "application/xml")

    def test_redact_headers(self) -> None:
        headers = {
            "X-Auth-Token": "real-secret",
            "Authorization": "Basic dXNlcjpwYXNz",
            "User-Agent": "test-agent",
        }
        redacted = MODULE.redact_headers(headers)
        self.assertEqual(redacted["X-Auth-Token"], "***")
        self.assertEqual(redacted["Authorization"], "Basic ***")
        self.assertEqual(redacted["User-Agent"], "test-agent")


class MinifluxHttpRequestSuccessTests(unittest.TestCase):
    def make_args(self, raw: bool = False, include_status: bool = False) -> argparse.Namespace:
        return argparse.Namespace(
            base_url="http://example.test",
            api_key="secret",
            username=None,
            password=None,
            timeout=30.0,
            method="GET",
            path="/v1/me",
            query=[],
            header=[],
            body_json=None,
            body_file=None,
            raw=raw,
            include_status=include_status,
            dry_run=False,
            command="request",
        )

    def run_request_with_response(
        self, args: argparse.Namespace, body: bytes, status: int = 200
    ) -> tuple[int, str]:
        stdout = StringIO()
        dummy = DummyHTTPResponse(body)
        dummy.status = status
        with patch.object(MODULE, "urlopen", return_value=dummy):
            with patch.object(sys, "stdout", stdout):
                code = MODULE.command_request(args)
        return code, stdout.getvalue()

    def test_request_success_json_response(self) -> None:
        args = self.make_args()
        body = b'{"id": 1, "username": "admin"}'
        code, stdout = self.run_request_with_response(args, body)
        self.assertEqual(code, 0)
        self.assertIn('"username"', stdout)
        self.assertIn('"admin"', stdout)

    def test_request_success_raw_response(self) -> None:
        args = self.make_args(raw=True)
        body = b"<?xml version=\"1.0\"?><opml></opml>"
        code, stdout = self.run_request_with_response(args, body)
        self.assertEqual(code, 0)
        self.assertIn("<?xml", stdout)
        self.assertTrue(stdout.endswith("\n"))

    def test_request_success_include_status(self) -> None:
        args = self.make_args(include_status=True)
        body = b'{"ok": true}'
        code, stdout = self.run_request_with_response(args, body)
        self.assertEqual(code, 0)
        self.assertIn("HTTP 200", stdout)


if __name__ == "__main__":
    unittest.main()
