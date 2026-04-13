# NanobotSkills

This repository stores local skills for AI coding agents (for example, Codex agents).

## Purpose

- Provide a minimal, machine-readable skill workspace.
- Let an agent quickly discover available skills and load each skill's `SKILL.md`.
- Keep skill logic modular by folder.

## Skill Entry Points

- Todo.txt skill directory: `todo-txt/`
- Main skill file: `todo-txt/SKILL.md`
- Timeline memory skill directory: `timeline-memory/`
- Main skill file: `timeline-memory/SKILL.md`
- SSH remote Docker ops skill directory: `ssh-remote-docker-ops/`
- Main skill file: `ssh-remote-docker-ops/SKILL.md`
- Miniflux HTTP API skill directory: `miniflux-http/`
- Main skill file: `miniflux-http/SKILL.md`

## How an Agent Should Use This Repo

1. Enumerate top-level skill folders.
2. Open each folder's `SKILL.md`.
3. Follow that skill's frontmatter and instructions to execute tasks.

## Current Skills

- `todo-txt`: Local todo.txt task management workflow (create/query/update/archive tasks).
- `timeline-memory`: Timeline memory workflow via `project-turn`/`get-thread`/`list-threads` CLI.
- `ssh-remote-docker-ops`: Remote Docker and Docker Compose operations over SSH, with read-first inspection and service-level management guidance.
- `miniflux-http`: Miniflux RSS API wrapper with authenticated HTTP operations and E2E testing support.

## Test Hygiene

- Prefer each skill's wrapper test commands over bare `pytest`; the wrappers isolate temp state per user and avoid repo-local pytest cache artifacts.
- `timeline-memory` uses `scripts/run-host-tests.py` for host/E2E stability runs and disables `cacheprovider` by default even for direct `pytest`.
- `miniflux-http` uses `scripts/run-tests.py` for pytest runs with isolated temp state.

## Path Conventions

- Keep path conventions skill-specific.
- Define concrete file paths inside each skill's `SKILL.md`.
- Treat this README as global guidance only.

## Skill Folder Naming Convention

- Use one skill per top-level folder.
- Prefer lowercase letters, digits, and hyphens (`kebab-case`), for example: `todo-txt`, `pdf-editor`.
- Keep folder names short and action/domain oriented.
- Each skill folder must include `SKILL.md` as the primary entry file.
