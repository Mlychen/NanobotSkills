# Miniflux HTTP Command Surface

Treat the command surface below as the stable interface for this skill. The same command can be executed through raw HTTP, through `scripts/miniflux_http.py`, or through another wrapper that preserves the same names and arguments.

## System

| Canonical command | Required args | Optional args | Mutation |
| --- | --- | --- | --- |
| `system health` | none | none | no |
| `system version` | none | none | no |
| `system counters` | none | none | no |
| `system discover` | `url` | none | no |
| `system export-opml` | none | none | no |
| `system flush-history` | none | none | yes |

## Feeds

| Canonical command | Required args | Optional args | Mutation |
| --- | --- | --- | --- |
| `feeds list` | none | none | no |
| `feeds get` | `feed_id` | none | no |
| `feeds create` | `feed_url` | `category_id`, `crawler`, `user_agent`, `username`, `password` | yes |
| `feeds delete` | `feed_id` | none | yes |
| `feeds refresh` | `feed_id` | none | yes |
| `feeds refresh-all` | none | none | yes |
| `feeds entries` | `feed_id` | `status`, `limit`, `offset`, `order`, `direction`, `published_after`, `published_before`, `changed_after`, `changed_before` | no |
| `feeds entry get` | `feed_id`, `entry_id` | none | no |
| `feeds icon` | `feed_id` | none | no |
| `feeds mark-read` | `feed_id` | none | yes |

## Entries

| Canonical command | Required args | Optional args | Mutation |
| --- | --- | --- | --- |
| `entries list` | none | `status`, `feed_id`, `category_id`, `limit`, `offset`, `order`, `direction`, `published_after`, `published_before`, `changed_after`, `changed_before` | no |
| `entries get` | `entry_id` | none | no |
| `entries set-status` | `entry_id`, `status` | none | yes |
| `entries toggle-bookmark` | `entry_id` | none | yes |
| `entries save` | `entry_id` | none | yes |
| `entries fetch-original` | `entry_id` | none | no |
| `entries mark-all-read` | `user_id` | none | yes |

## Categories

| Canonical command | Required args | Optional args | Mutation |
| --- | --- | --- | --- |
| `categories list` | none | none | no |
| `categories create` | `title` | none | yes |
| `categories update` | `category_id`, `title` | none | yes |
| `categories delete` | `category_id` | none | yes |
| `categories feeds` | `category_id` | none | no |
| `categories entries` | `category_id` | `status`, `limit`, `order`, `direction`, `published_after`, `published_before`, `changed_after`, `changed_before` | no |
| `categories entry get` | `category_id`, `entry_id` | none | no |
| `categories mark-read` | `category_id` | none | yes |
| `categories refresh` | `category_id` | none | yes |

## Users

| Canonical command | Required args | Optional args | Mutation |
| --- | --- | --- | --- |
| `users list` | none | none | no |
| `users me` | none | none | no |
| `users get` | `user_id` | none | no |
| `users by-username` | `username` | none | no |
| `users create` | `username`, `password` | `is_admin` | yes |
| `users delete` | `user_id` | none | yes |

## API Keys

| Canonical command | Required args | Optional args | Mutation |
| --- | --- | --- | --- |
| `api-keys list` | none | none | no |
| `api-keys create` | `description` | none | yes |
| `api-keys delete` | `api_key_id` | none | yes |

## Media

| Canonical command | Required args | Optional args | Mutation |
| --- | --- | --- | --- |
| `icons get` | `icon_id` | none | no |
| `enclosures get` | `enclosure_id` | none | no |

## Naming Rules

- Use plural collection names for top-level nouns: `feeds`, `entries`, `categories`, `users`, `api-keys`, `icons`, `enclosures`.
- Use `list` and `get` for reads; use explicit verbs such as `create`, `delete`, `refresh`, `set-status`, `mark-read`, and `toggle-bookmark` for writes.
- Keep nested lookups explicit: `feeds entry get` and `categories entry get`.
- Preserve the stable argument names used by this skill when serializing payloads or shell arguments: `feed_id`, `entry_id`, `category_id`, `user_id`, `api_key_id`, `feed_url`, `status`, `limit`, `offset`, `title`, and `description`.
- Resolve IDs with a read command before any write command unless the ID is already known from the current task.

## Observed HTTP Shapes

- The authenticated API surface is rooted at `/v1/`; unauthenticated access reaches the login UI and `/healthcheck`, not feed data.
- `system export-opml` maps to `GET /v1/export` and returns OPML XML.
- `entries list`, `feeds entries`, and `categories entries` return paginated JSON objects shaped like `{ "total": <number>, "entries": [...] }`.
- Date filtering uses Unix timestamps: `published_after`, `published_before`, `changed_after`, `changed_before` (available since Miniflux 2.0.49).
- `entries get`, `feeds entry get`, and `categories entry get` return a single entry object with nested `feed`, `enclosures`, and `tags`.
- `icons get` maps to a JSON wrapper shaped like `{ "id": <number>, "mime_type": <string>, "data": <string> }`; it is not a raw image byte stream.
- `enclosures get` returns enclosure metadata as JSON. Fetch the actual media from the enclosure's `url` field rather than expecting the API endpoint itself to stream the attachment.
- `system counters` remains part of the canonical command set, but this skill has not confirmed a raw HTTP route for it against the Miniflux `2.2.17` instance tested during authoring.
