from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def resolve_tmp_root(cli_tmp_root: str | None) -> Path:
    raw = cli_tmp_root or os.environ.get("TIMELINE_TEST_TMP_ROOT")
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
    else:
        path = ROOT / "tmp" / "test-runtime"
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean timeline-memory test artifacts")
    parser.add_argument("--tmp-root", help="Override TIMELINE_TEST_TMP_ROOT for cleanup")
    return parser.parse_args(argv)


def remove_child(path: Path) -> tuple[str, str]:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return ("cleaned", str(path))
    except Exception as exc:  # noqa: BLE001
        return ("denied", f"{path} ({exc})")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tmp_root = resolve_tmp_root(args.tmp_root)

    cleaned: list[str] = []
    denied: list[str] = []
    report_only: list[str] = []

    if tmp_root.exists():
        for child in sorted(tmp_root.iterdir()):
            status, detail = remove_child(child)
            if status == "cleaned":
                cleaned.append(detail)
            else:
                denied.append(detail)
    else:
        report_only.append(f"tmp root does not exist: {tmp_root}")

    for path in sorted(ROOT.glob("pytest-cache-files-*")):
        report_only.append(f"legacy pytest temp (report-only): {path}")

    print(f"[clean-test-artifacts] tmp_root={tmp_root}")
    print(f"[clean-test-artifacts] cleaned={len(cleaned)} denied={len(denied)} report_only={len(report_only)}")
    for item in cleaned:
        print(f"CLEANED  {item}")
    for item in denied:
        print(f"DENIED   {item}")
    for item in report_only:
        print(f"REPORT   {item}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
