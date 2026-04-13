from __future__ import annotations

import ast
import sys
from pathlib import Path

from scripts import timeline_cli


REPO_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_TOP_LEVEL_MODULES = {
    "errors",
    "models",
    "store",
    "test_runtime",
    "time_utils",
    "timeline_cli",
}


def test_importing_scripts_timeline_cli_does_not_register_top_level_internal_modules() -> None:
    assert timeline_cli.__name__ == "scripts.timeline_cli"
    leaked = sorted(name for name in FORBIDDEN_TOP_LEVEL_MODULES if name in sys.modules)
    assert leaked == []


def test_repo_disallows_top_level_internal_imports() -> None:
    violations: list[str] = []

    for path in sorted(REPO_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        relative_path = path.relative_to(REPO_ROOT)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN_TOP_LEVEL_MODULES:
                        violations.append(
                            f"{relative_path}:{node.lineno} imports forbidden top-level module {alias.name!r}"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module in FORBIDDEN_TOP_LEVEL_MODULES:
                    violations.append(
                        f"{relative_path}:{node.lineno} imports from forbidden top-level module {node.module!r}"
                    )

    assert violations == []
