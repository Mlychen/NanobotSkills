from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
TEST_TMP_ENV_VAR = "MINIFLUX_TEST_TMP_ROOT"

if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from test_runtime import resolve_tmp_root


DEFAULT_TEST_TMP_ROOT = resolve_tmp_root(
    ROOT,
    env_var_name=TEST_TMP_ENV_VAR,
    project_slug="miniflux-http",
)
os.environ.setdefault("PYTEST_DEBUG_TEMPROOT", str(DEFAULT_TEST_TMP_ROOT))
