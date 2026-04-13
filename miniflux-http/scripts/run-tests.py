from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

from test_runtime import build_test_env
from test_runtime import resolve_tmp_root


ROOT = Path(__file__).resolve().parents[1]
TEST_TMP_ENV_VAR = "MINIFLUX_TEST_TMP_ROOT"
DEFAULT_TESTS = ["tests"]


def resolve_pytest_runner() -> list[str]:
    if importlib.util.find_spec("pytest") is not None:
        return [sys.executable, "-m", "pytest"]
    raise RuntimeError("pytest is not available in the current Python environment")


def build_pytest_command(tmp_root: Path, extra_args: list[str]) -> list[str]:
    command = [
        *resolve_pytest_runner(),
        "--override-ini",
        "addopts=-q -p no:cacheprovider",
        "--basetemp",
        str(tmp_root / "pytest"),
    ]
    command.extend(DEFAULT_TESTS)
    command.extend(extra_args)
    return command


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run miniflux-http tests with isolated temp state")
    parser.add_argument("--tmp-root", help="Override MINIFLUX_TEST_TMP_ROOT")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="Additional pytest arguments")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tmp_root = resolve_tmp_root(
        ROOT,
        env_var_name=TEST_TMP_ENV_VAR,
        project_slug="miniflux-http",
        cli_tmp_root=args.tmp_root,
    )
    env = build_test_env(
        TEST_TMP_ENV_VAR,
        tmp_root,
        extra_env={
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
        },
    )

    extra_args = list(args.pytest_args)
    if extra_args and extra_args[0] == "--":
        extra_args = extra_args[1:]

    command = build_pytest_command(tmp_root, extra_args)
    print(f"[run-tests] tmp_root={tmp_root}")
    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
