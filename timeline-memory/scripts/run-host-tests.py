from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import importlib.util
from pathlib import Path

from test_runtime import build_test_env
from test_runtime import resolve_tmp_root


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODE = "sandbox-safe"
VALID_MODES = {"sandbox-safe", "standard"}
TEST_TMP_ENV_VAR = "TIMELINE_TEST_TMP_ROOT"
DEFAULT_TESTS = [
    "tests/timeline/test_timeline_cli_e2e.py",
    "tests/agent/test_timeline_memory_skill_integration.py",
]


def resolve_mode(cli_mode: str | None) -> str:
    mode = (cli_mode or os.environ.get("TIMELINE_TEST_MODE") or DEFAULT_MODE).strip().lower()
    if mode not in VALID_MODES:
        allowed = ", ".join(sorted(VALID_MODES))
        raise ValueError(f"unsupported test mode: {mode!r}; expected one of {allowed}")
    return mode

def resolve_pytest_runner() -> list[str]:
    if importlib.util.find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest"]
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv not found on PATH")
    return [uv, "run", "--extra", "dev", "python", "-m", "pytest"]


def build_pytest_command(mode: str, tmp_root: Path, extra_args: list[str]) -> list[str]:
    command = [
        *resolve_pytest_runner(),
        "--override-ini",
        "addopts=-q -p no:cacheprovider",
        "--basetemp",
        str(tmp_root / "pytest"),
    ]
    if mode == "sandbox-safe":
        command.extend(["-p", "no:tmpdir"])
    command.extend(DEFAULT_TESTS)
    command.extend(extra_args)
    return command


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run timeline-memory host-level tests")
    parser.add_argument("--mode", choices=sorted(VALID_MODES))
    parser.add_argument("--tmp-root", help="Override TIMELINE_TEST_TMP_ROOT")
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="Additional pytest arguments")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    mode = resolve_mode(args.mode)
    tmp_root = resolve_tmp_root(
        ROOT,
        env_var_name=TEST_TMP_ENV_VAR,
        project_slug="timeline-memory",
        cli_tmp_root=args.tmp_root,
    )
    env = build_test_env(
        TEST_TMP_ENV_VAR,
        tmp_root,
        extra_env={
            "TIMELINE_TEST_MODE": mode,
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
    )

    extra_args = list(args.pytest_args)
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]

    if args.rounds < 1:
        print("--rounds must be >= 1", file=sys.stderr)
        return 2

    for index in range(1, args.rounds + 1):
        print(f"[run-host-tests] round {index}/{args.rounds} mode={mode} tmp_root={tmp_root}")
        command = build_pytest_command(mode, tmp_root, extra_args)
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
