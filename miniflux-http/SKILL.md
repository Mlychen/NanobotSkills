---
name: miniflux-http
description: "Read and manage RSS feeds via Miniflux. Use when the user wants to view RSS feeds, check categories, read articles, subscribe to feeds, search entries, or manage Miniflux users and API keys. Triggered by: 查看RSS, 新闻, 订阅, feed, category, article, entry, bookmark."
---

# Miniflux HTTP

## Overview

Use this skill when the work should be expressed as stable Miniflux commands instead of ad hoc HTTP calls. Keep the command surface small and predictable, then map each command to a documented Miniflux HTTP request or to the helper script in this skill.

## Quick Start

- Reuse the standard environment variables: `MINIFLUX_URL`, `MINIFLUX_API_KEY`, `MINIFLUX_USERNAME`, and `MINIFLUX_PASSWORD`.
- Prefer API key authentication. Fall back to basic auth only when API key auth is unavailable.
- Start every session with a read-only handshake: run `python scripts/miniflux_http.py show-config`, then verify `/healthcheck`, then resolve `/v1/version` and `/v1/me` before mutating state.
- `show-config` is diagnostic: it always prints JSON, returns `0` when the configuration is request-ready, and returns `1` when required inputs are still missing.
- Use `python scripts/miniflux_http.py mark-read --all` to mark every unread entry for the current user as read in one step.
- Use `python scripts/miniflux_http.py mark-read --category-id 123` to mark all unread entries in a category as read.
- Use `python scripts/miniflux_http.py mark-read --category "Tech"` to resolve a category by name first, then mark that category as read.
- Add `--dry-run` to preview the resolved route first.
- Use [references/command-surface.md](references/command-surface.md) for the canonical command set.
- Use [references/repo-alignment.md](references/repo-alignment.md) for auth conventions, stable naming, and authoring-time HTTP notes.
- Use [scripts/miniflux_http.py](scripts/miniflux_http.py) for authenticated one-off requests or request previews instead of rewriting auth code.

## Workflow

1. Resolve connection and auth from environment or explicit arguments.
2. Pick the canonical command from [references/command-surface.md](references/command-surface.md) before touching transport details.
3. Resolve IDs with `list` or `get` commands before calling write or delete operations.
4. Preview non-trivial mutations with `python scripts/miniflux_http.py request --dry-run ...` when the request is hand-built.
5. Normalize outputs as JSON when possible; preserve raw text only for OPML or original article content.

## Command Families

- `system`: health, version, counters, discovery, export, history flush
- `feeds`: list, get, create, delete, refresh, nested entry lookups
- `entries`: list, get, status changes, bookmark/save actions, original content
- `categories`: list, create, update, delete, nested feed and entry lookups
- `users`: current user, lookup, create, delete
- `api-keys`: list, create, delete
- `media`: icon and enclosure fetches

Read the full catalog in [references/command-surface.md](references/command-surface.md). The command naming is intentionally stable even if the transport becomes raw HTTP, a script, or another wrapper.

## Resource Guide

- [references/command-surface.md](references/command-surface.md): Canonical commands, arguments, mutation level, and observed HTTP response shapes.
- [references/repo-alignment.md](references/repo-alignment.md): Naming rules, auth conventions, and authoring-time validation notes.
- [scripts/miniflux_http.py](scripts/miniflux_http.py): Authenticated request helper with request preview, configuration diagnostics, and JSON pretty printing.
