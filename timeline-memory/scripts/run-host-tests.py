from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODE = "sandbox-safe"
VALID_MODES = {"sandbox-safe", "standard"}
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


def resolve_tmp_root(cli_tmp_root: str | None) -> Path:
    raw = cli_tmp_root or os.environ.get("TIMELINE_TEST_TMP_ROOT")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
    else:
        path = ROOT / "tmp" / "test-runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_env(mode: str, tmp_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["TIMELINE_TEST_MODE"] = mode
    env["TIMELINE_TEST_TMP_ROOT"] = str(tmp_root)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["TMP"] = str(tmp_root)
    env["TEMP"] = str(tmp_root)
    env["TMPDIR"] = str(tmp_root)
    return env


def build_pytest_command(mode: str, extra_args: list[str]) -> list[str]:
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv not found on PATH")
    command = [uv, "run", "--extra", "dev", "python", "-m", "pytest", "--override-ini", "addopts=-q"]
    if mode == "sandbox-safe":
        command.extend(["-p", "no:tmpdir", "-p", "no:cacheprovider"])
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
    tmp_root = resolve_tmp_root(args.tmp_root)
    env = build_env(mode, tmp_root)

    extra_args = list(args.pytest_args)
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]

    if args.rounds < 1:
        print("--rounds must be >= 1", file=sys.stderr)
        return 2

    for index in range(1, args.rounds + 1):
        print(f"[run-host-tests] round {index}/{args.rounds} mode={mode} tmp_root={tmp_root}")
        command = build_pytest_command(mode, extra_args)
        result = subprocess.run(command, cwd=ROOT, env=env, check=False)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
