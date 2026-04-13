from __future__ import annotations

import os
import tempfile
from pathlib import Path


APP_STATE_DIR = "NanobotSkills"
TMP_ENV_VARS = ("TMP", "TEMP", "TMPDIR", "PYTEST_DEBUG_TEMPROOT")


def resolve_tmp_root(
    project_root: Path,
    *,
    env_var_name: str,
    project_slug: str,
    cli_tmp_root: str | None = None,
) -> Path:
    raw = cli_tmp_root or os.environ.get(env_var_name)
    if raw:
        path = Path(raw)
        if not path.is_absolute():
            path = project_root / path
    else:
        base = _resolve_user_state_root()
        path = base / APP_STATE_DIR / project_slug / "test-runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def build_test_env(
    env_var_name: str,
    tmp_root: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ)
    env[env_var_name] = str(tmp_root)
    for key in TMP_ENV_VARS:
        env[key] = str(tmp_root)
    if extra_env:
        env.update(extra_env)
    return env


def resolve_test_home(tmp_root: Path) -> Path:
    path = tmp_root / "home"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_user_state_root() -> Path:
    return Path(tempfile.gettempdir())
