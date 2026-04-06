from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ALLOWED_COMMANDS = {"project-turn", "get-thread", "list-threads", "list-thread-history"}
SCRIPT_DIR = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from store import acquire_project_turn_write_lock


def resolve_python_command() -> list[str]:
    if sys.executable and Path(sys.executable).exists():
        return [sys.executable]
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("current python executable is unavailable and uv not found on PATH")
    return [uv, "run", "python"]


class TimelineMemoryHostAdapter:
    def __init__(self, skill_root: Path, *, tmp_root: Path, test_mode: str) -> None:
        self.skill_root = skill_root
        self.tmp_root = tmp_root
        self.test_mode = test_mode
        self.cli_path = self.discover_cli()

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

    def discover_cli(self) -> Path:
        cli_path = self.skill_root / "scripts" / "timeline_cli.py"
        if not cli_path.exists():
            raise FileNotFoundError(f"timeline_cli.py not found under {self.skill_root}")
        return cli_path

    def build_command(
        self,
        command: str,
        *,
        store_root: Path | None,
        input_path: Path | None = None,
        args: list[str] | None = None,
    ) -> list[str]:
        if command not in ALLOWED_COMMANDS:
            raise ValueError(f"unsupported command: {command}")
        if store_root is None:
            raise ValueError("store_root is required")
        if command == "project-turn" and input_path is None:
            raise ValueError("project-turn requires input_path")
        if command != "project-turn" and input_path is not None:
            raise ValueError(f"{command} does not accept input_path")

        argv = [*resolve_python_command(), str(self.cli_path), command, "--store-root", str(store_root)]
        if input_path is not None:
            argv.extend(["--input", str(input_path)])
        if args:
            argv.extend(args)
        return argv

    def invoke_json(
        self,
        command: str,
        *,
        store_root: Path,
        payload: dict | None = None,
        args: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> dict | list | None:
        input_path = None
        if payload is not None:
            input_path = store_root.parent / f"host-{command}-input.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        argv = self.build_command(command, store_root=store_root, input_path=input_path, args=args)
        result = subprocess.run(
            argv,
            cwd=self.skill_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**self._env(), **(extra_env or {})},
            check=False,
        )
        assert result.returncode == 0, result.stderr.strip() or f"command failed: {command}"
        stdout = result.stdout.strip()
        return json.loads(stdout) if stdout else None

    def start_process(
        self,
        command: str,
        *,
        store_root: Path,
        payload: dict | None = None,
        args: list[str] | None = None,
        extra_env: dict[str, str] | None = None,
        input_name: str | None = None,
    ) -> subprocess.Popen[str]:
        input_path = None
        if payload is not None:
            filename = input_name or f"host-{command}-input.json"
            input_path = store_root.parent / filename
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        argv = self.build_command(command, store_root=store_root, input_path=input_path, args=args)
        return subprocess.Popen(
            argv,
            cwd=self.skill_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={**self._env(), **(extra_env or {})},
        )
def test_discovery_failure_reports_missing_cli(scratch_root: Path, test_mode: str) -> None:
    with pytest.raises(FileNotFoundError, match="timeline_cli.py"):
        TimelineMemoryHostAdapter(scratch_root / "missing-skill", tmp_root=scratch_root, test_mode=test_mode)


def test_command_injection_requires_store_root_and_input(repo_root: Path, scratch_root: Path, test_mode: str) -> None:
    adapter = TimelineMemoryHostAdapter(repo_root, tmp_root=scratch_root, test_mode=test_mode)

    with pytest.raises(ValueError, match="store_root is required"):
        adapter.build_command("list-threads", store_root=None)
    with pytest.raises(ValueError, match="project-turn requires input_path"):
        adapter.build_command("project-turn", store_root=scratch_root / "store")
    with pytest.raises(ValueError, match="unsupported command"):
        adapter.build_command("append-raw-event", store_root=scratch_root / "store")

    command = adapter.build_command(
        "project-turn",
        store_root=scratch_root / "store",
        input_path=scratch_root / "input.json",
    )
    assert "--store-root" in command
    assert "--input" in command


def test_host_adapter_e2e_write_and_read_contract(repo_root: Path, scratch_root: Path, test_mode: str) -> None:
    adapter = TimelineMemoryHostAdapter(repo_root, tmp_root=scratch_root, test_mode=test_mode)
    store_root = scratch_root / "host-store"
    payload = {
        "turn_id": "agent:host:0001",
        "user_text": "记录一次宿主调用。",
        "assistant_text": "已记录。",
        "thread": {"thread_id": "thr_host_1", "title": "host-thread", "status": "planned"},
    }

    created = adapter.invoke_json("project-turn", store_root=store_root, payload=payload)
    thread = adapter.invoke_json("get-thread", store_root=store_root, args=["--thread-id", "thr_host_1"])
    threads = adapter.invoke_json("list-threads", store_root=store_root, args=["--status", "planned"])
    history = adapter.invoke_json("list-thread-history", store_root=store_root, args=["--thread-id", "thr_host_1"])

    assert created["ok"] is True
    assert created["recorded_event_ids"] == ["agent:host:0001:in", "agent:host:0001:out"]
    assert thread["title"] == "host-thread"
    assert len(threads) == 1
    assert history == []


def test_host_adapter_reports_busy_writer_timeout(repo_root: Path, scratch_root: Path, test_mode: str) -> None:
    adapter = TimelineMemoryHostAdapter(repo_root, tmp_root=scratch_root, test_mode=test_mode)
    store_root = scratch_root / "host-busy-store"
    second_payload = {
        "turn_id": "agent:host:busy:0002",
        "user_text": "第二个写者。",
        "assistant_text": "应该超时。",
        "thread": {"thread_id": "thr_host_busy", "title": "busy-thread-2", "status": "planned"},
    }

    with acquire_project_turn_write_lock(
        store_root,
        turn_id="agent:host:busy:hold",
        thread_id="thr_host_busy",
    ):
        second_process = adapter.start_process(
            "project-turn",
            store_root=store_root,
            payload=second_payload,
            input_name="host-busy-second.json",
            extra_env={"TIMELINE_PROJECT_TURN_WRITE_LOCK_TIMEOUT_SECONDS": "0.1"},
        )
        second_stdout, second_stderr = second_process.communicate(timeout=10)

    assert second_process.returncode == 1
    assert second_stdout.strip() == ""
    error = json.loads(second_stderr)
    assert error["error"]["code"] == "TM_STORE_BUSY"
    assert error["error"]["category"] == "busy"
    assert error["error"]["details"]["retryable"] is True
    assert error["error"]["details"]["turn_id"] == second_payload["turn_id"]
    assert "store is busy with another writer" in error["error"]["message"]
