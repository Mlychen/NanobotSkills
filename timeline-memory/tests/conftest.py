from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from unittest import mock

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = REPO_ROOT / "scripts" / "timeline_cli.py"
SCRIPTS_DIR = REPO_ROOT / "scripts"
DEFAULT_TEST_MODE = "sandbox-safe"
VALID_TEST_MODES = {"sandbox-safe", "standard"}

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import timeline_cli


def _resolve_python_command() -> list[str]:
    if sys.executable and Path(sys.executable).exists():
        return [sys.executable]
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("current python executable is unavailable and uv not found on PATH")
    return [uv, "run", "python"]


class CliRunner:
    def __init__(self, cli_path: Path, repo_root: Path, tmp_root: Path, test_mode: str) -> None:
        self.cli_path = cli_path
        self.repo_root = repo_root
        self.tmp_root = tmp_root
        self.test_mode = test_mode

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["TIMELINE_TEST_MODE"] = self.test_mode
        env["TIMELINE_TEST_TMP_ROOT"] = str(self.tmp_root)
        env["TMP"] = str(self.tmp_root)
        env["TEMP"] = str(self.tmp_root)
        env["TMPDIR"] = str(self.tmp_root)
        return env

    def run_process(
        self,
        store_root: Path,
        command: str,
        *,
        payload: dict | None = None,
        args: list[str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        argv = [command, "--store-root", str(store_root)]
        if args:
            argv.extend(args)
        if payload is not None:
            input_path = store_root.parent / f"{command}-input.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            argv.extend(["--input", str(input_path)])
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, self._env(), clear=False),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            try:
                returncode = timeline_cli.main(argv)
            except SystemExit as exc:
                code = exc.code
                returncode = code if isinstance(code, int) else 1
        return subprocess.CompletedProcess(
            args=[*_resolve_python_command(), str(self.cli_path), *argv],
            returncode=returncode,
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue(),
        )

    def run_json(
        self,
        store_root: Path,
        command: str,
        *,
        payload: dict | None = None,
        args: list[str] | None = None,
    ) -> dict | list | None:
        result = self.run_process(store_root, command, payload=payload, args=args)
        assert result.returncode == 0, result.stderr.strip() or f"command failed: {command}"
        stdout = result.stdout.strip()
        return json.loads(stdout) if stdout else None

    def expect_failure(
        self,
        store_root: Path,
        command: str,
        *,
        payload: dict | None = None,
        args: list[str] | None = None,
    ) -> str:
        result = self.run_process(store_root, command, payload=payload, args=args)
        assert result.returncode != 0, f"expected command to fail: {command}"
        return result.stderr.strip()


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def cli_path() -> Path:
    assert CLI_PATH.exists(), f"timeline cli script missing: {CLI_PATH}"
    return CLI_PATH


@pytest.fixture
def cli_runner(cli_path: Path, repo_root: Path, test_tmp_root: Path, test_mode: str) -> CliRunner:
    return CliRunner(cli_path, repo_root=repo_root, tmp_root=test_tmp_root, test_mode=test_mode)


def _resolve_test_mode() -> str:
    mode = os.environ.get("TIMELINE_TEST_MODE", DEFAULT_TEST_MODE).strip().lower()
    if mode not in VALID_TEST_MODES:
        allowed = ", ".join(sorted(VALID_TEST_MODES))
        raise RuntimeError(f"unsupported TIMELINE_TEST_MODE={mode!r}; expected one of: {allowed}")
    return mode


def _resolve_test_tmp_root(repo_root: Path) -> Path:
    raw = os.environ.get("TIMELINE_TEST_TMP_ROOT")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = repo_root / path
    else:
        path = repo_root / "tmp" / "test-runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture(scope="session")
def test_mode() -> str:
    return _resolve_test_mode()


@pytest.fixture(scope="session")
def test_tmp_root(repo_root: Path) -> Path:
    return _resolve_test_tmp_root(repo_root)


@pytest.fixture
def scratch_root(test_tmp_root: Path) -> Path:
    path = test_tmp_root / f"test-run-{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
