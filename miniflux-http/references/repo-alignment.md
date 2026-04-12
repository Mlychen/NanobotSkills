# Skill Conventions

This note captures the stable conventions used by this skill so it can remain self-contained and portable across workspaces.

## Auth Conventions

- `MINIFLUX_URL` is always required.
- Prefer `MINIFLUX_API_KEY`.
- Fall back to `MINIFLUX_USERNAME` plus `MINIFLUX_PASSWORD`.
- Keep this precedence in any wrapper built around the skill so behavior stays predictable.

## Design Constraints

- Keep the skill command surface stable even if the transport changes.
- Preserve stable field names instead of inventing new aliases.
- Separate read and write verbs so destructive operations remain obvious in prompts and logs.
- Use explicit multiword verbs such as `refresh-all`, `mark-read`, `set-status`, `fetch-original`, and `flush-history`.
- Prefer collection-first commands because they group naturally in references and shell completion.
- Treat command naming and raw HTTP shapes as related but distinct: the same capability may surface through different route shapes or response wrappers across Miniflux versions or wrappers.

## Execution Guidance

- For one-off HTTP calls, use `scripts/miniflux_http.py` and pass the exact request path and payload.
- For design or implementation work, map from the canonical command to a verified HTTP route first, then decide whether another wrapper is still necessary.
- When exact HTTP route shapes matter, confirm them against the target Miniflux version rather than assuming parity from command names alone.

## Observed Live Behavior

- The authoring-time verification target was Miniflux `2.2.17` at `http://winnas:9090/`.
- Verified authenticated routes included `/v1/version`, `/v1/me`, `/v1/feeds`, `/v1/categories`, `/v1/entries`, `/v1/export`, `/v1/icons/{id}`, and `/v1/enclosures/{id}`.
- `/v1/export` exists and returns OPML XML.
- Feed and category entry listing routes return paginated envelopes with `total` and `entries`.
- Media routes are JSON wrappers rather than raw binary downloads: icons expose `id`, `mime_type`, and `data`; enclosures expose metadata and should be dereferenced via their `url`.
- A raw HTTP route for `system counters` was not confirmed during live testing, so keep that command documented as part of the canonical interface rather than a guaranteed HTTP path.
