# NanobotSkills

This repository stores local skills for LLM agents.

## Purpose

- Provide a minimal, machine-readable skill workspace.
- Let an LLM quickly discover available skills and load each skill's `SKILL.md`.
- Keep skill logic modular by folder.

## Skill Entry Points

- Todo.txt skill directory: `Todotxt/`
- Main skill file: `Todotxt/SKILL.md`

## How an LLM Should Use This Repo

1. Enumerate top-level skill folders.
2. Open each folder's `SKILL.md`.
3. Follow that skill's frontmatter and instructions to execute tasks.

## Current Skills

- `todo-txt`: Local todo.txt task management workflow (create/query/update/archive tasks).

## Skill Folder Naming Convention

- Use one skill per top-level folder.
- Prefer lowercase letters, digits, and hyphens (`kebab-case`), for example: `todo-txt`, `pdf-editor`.
- Keep folder names short and action/domain oriented.
- Each skill folder must include `SKILL.md` as the primary entry file.
