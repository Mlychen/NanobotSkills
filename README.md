# NanobotSkills

This repository stores local skills for AI coding agents (for example, Codex agents).

## Purpose

- Provide a minimal, machine-readable skill workspace.
- Let an agent quickly discover available skills and load each skill's `SKILL.md`.
- Keep skill logic modular by folder.

## Skill Entry Points

- Todo.txt skill directory: `todo-txt/`
- Main skill file: `todo-txt/SKILL.md`

## How an Agent Should Use This Repo

1. Enumerate top-level skill folders.
2. Open each folder's `SKILL.md`.
3. Follow that skill's frontmatter and instructions to execute tasks.

## Current Skills

- `todo-txt`: Local todo.txt task management workflow (create/query/update/archive tasks).

## Path Conventions

- The `todo-txt` skill uses fixed files:
- `~/.nanobot/workspace/todo.txt`
- `~/.nanobot/workspace/done.txt`

## Skill Folder Naming Convention

- Use one skill per top-level folder.
- Prefer lowercase letters, digits, and hyphens (`kebab-case`), for example: `todo-txt`, `pdf-editor`.
- Keep folder names short and action/domain oriented.
- Each skill folder must include `SKILL.md` as the primary entry file.
