# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Repository Overview

This is a **skill workspace for AI coding agents**. Each top-level folder is a self-contained skill with its own `SKILL.md` entry point. Agents should enumerate skill folders, read each `SKILL.md`, and follow the instructions within.

## Skills

| Skill | Directory | Purpose |
|-------|-----------|---------|
| todo-txt | `todo-txt/` | Plain-text task management (todo.txt format) |
| timeline-memory | `timeline-memory/` | Timeline-based memory persistence via Python CLI |
| ssh-remote-docker-ops | `ssh-remote-docker-ops/` | Remote Docker/Compose operations over SSH |
| miniflux-http | `miniflux-http/` | Miniflux RSS API wrapper and HTTP operations |

## Skill Folder Structure

Each skill follows this pattern:
```
<skill-name>/
  SKILL.md              # Primary entry point (YAML frontmatter + instructions)
  scripts/              # Executable scripts (CLI tools, helpers)
  tests/                # (optional) Test suites
  references/           # (optional) Design docs, schemas, command catalogs
  pyproject.toml        # (optional) Python project metadata for uv
```

## Development Commands

### timeline-memory

Requires `uv` installed. All commands run from `timeline-memory/` directory.

```bash
# Daily dev regression (store primitives + E2E + host integration)
uv run --extra dev python -m pytest -q tests/timeline/test_store_primitives.py tests/timeline/test_timeline_cli_e2e.py tests/agent/test_timeline_memory_skill_integration.py

# Host-level stability tests
uv run python scripts/run-host-tests.py

# Host-level stability tests, 3 rounds
uv run python scripts/run-host-tests.py --rounds 3

# Pre-release self-check (standalone bundle)
uv run python scripts/selftest.py

# Full pre-release regression
uv run --extra dev python -m pytest -q tests/timeline/test_store_primitives.py tests/timeline/test_timeline_cli_e2e.py tests/agent/test_timeline_memory_skill_integration.py
uv run python scripts/selftest.py
uv run python scripts/run-host-tests.py --rounds 3

# Clean test artifacts
uv run python scripts/clean-test-artifacts.py
```

Key architecture: `scripts/timeline_cli.py` is the sole CLI entry point, backed by `store.py` (persistence), `models.py` (data models), and `time_utils.py`. The schema is defined in `references/schema.md`.

### miniflux-http

```bash
# Config diagnostic (returns 0 if ready, 1 if missing inputs)
python scripts/miniflux_http.py show-config

# Preview request without sending
python scripts/miniflux_http.py request --dry-run ...
```

Environment: `MINIFLUX_URL`, `MINIFLUX_API_KEY`, `MINIFLUX_USERNAME`, `MINIFLUX_PASSWORD`. Read the full command catalog in `references/command-surface.md`.

## Path Conventions

- One skill per top-level folder
- Folder names use kebab-case (e.g., `todo-txt`, `timeline-memory`)
- All skill-specific instructions live inside `SKILL.md`
- This README is global guidance only
