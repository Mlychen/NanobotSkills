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
from urllib.error import URLError


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "miniflux_http.py"
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from test_runtime import build_test_env
from test_runtime import resolve_test_home
from test_runtime import resolve_tmp_root


TEST_TMP_ENV_VAR = "MINIFLUX_TEST_TMP_ROOT"
TEST_TMP_ROOT = resolve_tmp_root(ROOT, env_var_name=TEST_TMP_ENV_VAR, project_slug="miniflux-http")


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
        cli_env = build_test_env(TEST_TMP_ENV_VAR, TEST_TMP_ROOT)
        for key in (
            "MINIFLUX_URL",
            "MINIFLUX_API_KEY",
            "MINIFLUX_USERNAME",
            "MINIFLUX_PASSWORD",
        ):
            cli_env.pop(key, None)
        if env:
            cli_env.update(env)
        temp_home = str(resolve_test_home(TEST_TMP_ROOT))
        cli_env["HOME"] = temp_home
        cli_env["USERPROFILE"] = temp_home
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

    def test_mark_read_without_scope_returns_usage_error(self) -> None:
        result = self.run_cli(
            "mark-read",
            "--base-url",
            "http://example.test",
            "--api-key",
            "secret",
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("one of the arguments --all --category-id --category is required", result.stderr)


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
            title_only=False,
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
            title_only=False,
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


class MinifluxHttpMarkReadTests(unittest.TestCase):
    def make_args(
        self,
        *,
        all_entries: bool = True,
        category_id: int | None = None,
        category: str | None = None,
        user_id: int | None = None,
        dry_run: bool = False,
        include_status: bool = False,
    ) -> argparse.Namespace:
        return argparse.Namespace(
            base_url="http://example.test",
            api_key="secret",
            username=None,
            password=None,
            timeout=30.0,
            all=all_entries,
            category_id=category_id,
            category=category,
            user_id=user_id,
            include_status=include_status,
            dry_run=dry_run,
            command="mark-read",
        )

    def test_mark_read_all_dry_run_uses_explicit_user_id(self) -> None:
        stdout = StringIO()
        args = self.make_args(user_id=9, dry_run=True)

        with patch.object(sys, "stdout", stdout):
            code = MODULE.command_mark_read(args)

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["method"], "PUT")
        self.assertEqual(
            payload["url"], "http://example.test/v1/users/9/mark-all-as-read"
        )
        self.assertFalse(payload["has_body"])

    def test_mark_read_category_dry_run_targets_category_route(self) -> None:
        stdout = StringIO()
        args = self.make_args(all_entries=False, category_id=12, dry_run=True)

        with patch.object(sys, "stdout", stdout):
            code = MODULE.command_mark_read(args)

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["method"], "PUT")
        self.assertEqual(
            payload["url"], "http://example.test/v1/categories/12/mark-all-as-read"
        )
        self.assertFalse(payload["has_body"])

    def test_mark_read_category_name_dry_run_resolves_category_id(self) -> None:
        stdout = StringIO()
        args = self.make_args(all_entries=False, category="Tech", dry_run=True)
        categories_response = DummyHTTPResponse(
            json.dumps(
                [
                    {"id": 12, "title": "Tech"},
                    {"id": 15, "title": "News"},
                ]
            ).encode()
        )
        requests: list[object] = []

        def fake_urlopen(request, timeout):  # noqa: ANN001
            requests.append(request)
            return categories_response

        with patch.object(MODULE, "urlopen", side_effect=fake_urlopen):
            with patch.object(sys, "stdout", stdout):
                code = MODULE.command_mark_read(args)

        self.assertEqual(code, 0)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].full_url, "http://example.test/v1/categories")
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["method"], "PUT")
        self.assertEqual(
            payload["url"], "http://example.test/v1/categories/12/mark-all-as-read"
        )
        self.assertFalse(payload["has_body"])

    def test_mark_read_all_resolves_user_from_me(self) -> None:
        args = self.make_args()
        me_response = DummyHTTPResponse(b'{"id": 7, "username": "admin"}')
        mark_response = DummyHTTPResponse(b"")
        requests: list[object] = []

        def fake_urlopen(request, timeout):  # noqa: ANN001
            requests.append(request)
            if len(requests) == 1:
                return me_response
            return mark_response

        with patch.object(MODULE, "urlopen", side_effect=fake_urlopen):
            code = MODULE.command_mark_read(args)

        self.assertEqual(code, 0)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].full_url, "http://example.test/v1/me")
        self.assertEqual(requests[0].method, "GET")
        self.assertEqual(
            requests[1].full_url,
            "http://example.test/v1/users/7/mark-all-as-read",
        )
        self.assertEqual(requests[1].method, "PUT")

    def test_mark_read_category_does_not_resolve_user(self) -> None:
        args = self.make_args(all_entries=False, category_id=12)
        category_response = DummyHTTPResponse(b"")
        requests: list[object] = []

        def fake_urlopen(request, timeout):  # noqa: ANN001
            requests.append(request)
            return category_response

        with patch.object(MODULE, "urlopen", side_effect=fake_urlopen):
            code = MODULE.command_mark_read(args)

        self.assertEqual(code, 0)
        self.assertEqual(len(requests), 1)
        self.assertEqual(
            requests[0].full_url,
            "http://example.test/v1/categories/12/mark-all-as-read",
        )
        self.assertEqual(requests[0].method, "PUT")

    def test_mark_read_category_name_is_case_insensitive(self) -> None:
        args = self.make_args(all_entries=False, category="tech")
        categories_response = DummyHTTPResponse(
            json.dumps(
                [
                    {"id": 12, "title": "Tech"},
                    {"id": 15, "title": "News"},
                ]
            ).encode()
        )
        category_response = DummyHTTPResponse(b"")
        requests: list[object] = []

        def fake_urlopen(request, timeout):  # noqa: ANN001
            requests.append(request)
            if len(requests) == 1:
                return categories_response
            return category_response

        with patch.object(MODULE, "urlopen", side_effect=fake_urlopen):
            code = MODULE.command_mark_read(args)

        self.assertEqual(code, 0)
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].full_url, "http://example.test/v1/categories")
        self.assertEqual(
            requests[1].full_url,
            "http://example.test/v1/categories/12/mark-all-as-read",
        )
        self.assertEqual(requests[1].method, "PUT")

    def test_mark_read_resolve_user_requires_integer_id(self) -> None:
        args = self.make_args()

        with patch.object(MODULE, "urlopen", return_value=DummyHTTPResponse(b'{"username": "admin"}')):
            with self.assertRaises(MODULE.RequestFailureError):
                MODULE.command_mark_read(args)

    def test_mark_read_category_name_requires_match(self) -> None:
        args = self.make_args(all_entries=False, category="Missing")

        with patch.object(MODULE, "urlopen", return_value=DummyHTTPResponse(b'[]')):
            with self.assertRaisesRegex(MODULE.CliUsageError, "Category not found: Missing"):
                MODULE.command_mark_read(args)

    def test_mark_read_category_name_rejects_ambiguous_match(self) -> None:
        args = self.make_args(all_entries=False, category="tech")
        body = json.dumps(
            [
                {"id": 12, "title": "Tech"},
                {"id": 13, "title": "TECH"},
            ]
        ).encode()

        with patch.object(MODULE, "urlopen", return_value=DummyHTTPResponse(body)):
            with self.assertRaisesRegex(MODULE.CliUsageError, "Category name is ambiguous: tech"):
                MODULE.command_mark_read(args)

    def test_main_mark_read_all_lookup_network_failure_returns_request_error_exit_code(self) -> None:
        stderr = StringIO()
        argv = [
            str(SCRIPT),
            "mark-read",
            "--base-url",
            "http://example.test",
            "--api-key",
            "secret",
            "--all",
        ]

        with patch.object(sys, "argv", argv):
            with patch.object(MODULE, "urlopen", side_effect=URLError("connection refused")):
                with patch.object(sys, "stderr", stderr):
                    code = MODULE.main()

        self.assertEqual(code, 1)
        self.assertIn("Unable to resolve current user from /v1/me", stderr.getvalue())

    def test_main_mark_read_category_lookup_http_failure_returns_request_error_exit_code(self) -> None:
        stderr = StringIO()
        argv = [
            str(SCRIPT),
            "mark-read",
            "--base-url",
            "http://example.test",
            "--api-key",
            "secret",
            "--category",
            "Tech",
        ]
        error = HTTPError(
            url="http://example.test/v1/categories",
            code=500,
            msg="Internal Server Error",
            hdrs=None,
            fp=DummyHTTPResponse(b""),
        )

        with patch.object(sys, "argv", argv):
            with patch.object(MODULE, "urlopen", side_effect=error):
                with patch.object(sys, "stderr", stderr):
                    code = MODULE.main()

        self.assertEqual(code, 1)
        self.assertIn("Unable to resolve category from /v1/categories: HTTP 500", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()


class MinifluxHttpRequestTitleOnlyTests(unittest.TestCase):
    """Tests for --title-only client-side content stripping."""

    def make_args(self) -> argparse.Namespace:
        return argparse.Namespace(
            base_url="http://example.test",
            api_key="secret",
            username=None,
            password=None,
            timeout=30.0,
            method="GET",
            path="/v1/entries",
            query=[],
            header=[],
            body_json=None,
            body_file=None,
            raw=False,
            include_status=False,
            dry_run=False,
            title_only=True,
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

    def test_title_only_strips_paginated_entries(self) -> None:
        args = self.make_args()
        body = json.dumps({
            "total": 2,
            "entries": [
                {"id": 1, "title": "First", "content": "<p>Full text</p>", "summary": "Short", "hash": "abc", "url": "http://x"},
                {"id": 2, "title": "Second", "content": "<p>More text</p>", "hash": "def", "url": "http://y"},
            ],
        }).encode()
        code, stdout = self.run_request_with_response(args, body)
        self.assertEqual(code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["total"], 2)
        for entry in data["entries"]:
            self.assertNotIn("content", entry)
            self.assertNotIn("summary", entry)
            self.assertNotIn("hash", entry)
            self.assertIn("id", entry)
            self.assertIn("title", entry)
            self.assertIn("url", entry)

    def test_title_only_strips_single_entry(self) -> None:
        args = self.make_args()
        body = json.dumps({
            "id": 42,
            "title": "Solo",
            "content": "<p>Huge article body</p>",
            "summary": "tl;dr",
            "hash": "xyz",
            "feed": {"id": 1, "title": "Feed A"},
        }).encode()
        code, stdout = self.run_request_with_response(args, body)
        self.assertEqual(code, 0)
        data = json.loads(stdout)
        self.assertNotIn("content", data)
        self.assertNotIn("summary", data)
        self.assertNotIn("hash", data)
        self.assertEqual(data["title"], "Solo")
        self.assertIn("feed", data)

    def test_title_only_non_entry_response_unchanged(self) -> None:
        """Non-entry dicts (e.g. /v1/me) should pass through unchanged."""
        args = self.make_args()
        body = json.dumps({"id": 1, "username": "admin", "is_admin": True}).encode()
        code, stdout = self.run_request_with_response(args, body)
        self.assertEqual(code, 0)
        data = json.loads(stdout)
        self.assertEqual(data["username"], "admin")

    def test_title_only_preserves_nested_objects(self) -> None:
        """Nested objects like feed, enclosures, tags should remain."""
        args = self.make_args()
        body = json.dumps({
            "id": 10,
            "title": "Nested Test",
            "content": "<p>body</p>",
            "feed": {"id": 3, "title": "Tech"},
            "enclosures": [{"url": "http://file.mp3", "mime_type": "audio/mpeg"}],
            "tags": ["python"],
        }).encode()
        code, stdout = self.run_request_with_response(args, body)
        self.assertEqual(code, 0)
        data = json.loads(stdout)
        self.assertNotIn("content", data)
        self.assertEqual(data["feed"]["title"], "Tech")
        self.assertEqual(len(data["enclosures"]), 1)
        self.assertEqual(data["tags"], ["python"])

    def test_title_only_false_preserves_content(self) -> None:
        args = self.make_args()
        args.title_only = False
        body = json.dumps({
            "id": 1,
            "title": "Full",
            "content": "<p>All of it</p>",
            "summary": "Brief",
        }).encode()
        code, stdout = self.run_request_with_response(args, body)
        self.assertEqual(code, 0)
        data = json.loads(stdout)
        self.assertIn("content", data)
        self.assertIn("summary", data)


class MinifluxHttpStripBodyUnitTests(unittest.TestCase):
    """Unit tests for strip_body helper."""

    def test_strips_body_fields(self) -> None:
        entry = {
            "id": 1, "title": "T", "content": "x",
            "summary": "y", "hash": "z", "url": "u",
        }
        result = MODULE.strip_body(entry)
        self.assertEqual(set(result.keys()), {"id", "title", "url"})

    def test_preserves_other_fields(self) -> None:
        entry = {
            "id": 2, "title": "T", "feed": {"id": 5},
            "tags": ["a"], "status": "unread",
            "content": "gone",
        }
        result = MODULE.strip_body(entry)
        self.assertIn("feed", result)
        self.assertIn("tags", result)
        self.assertNotIn("content", result)
