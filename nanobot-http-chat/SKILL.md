---
name: nanobot-http-chat
description: "Delegate text prompts to a remote nanobot HTTP endpoint. Use when another agent needs to relay a prompt through nanobot, continue a remote nanobot session by explicit session_id, validate a reachable nanobot chat endpoint, or use nanobot as a downstream conversational agent over LAN/VPN or another internal URL. Triggered by: nanobot API, relay this to nanobot, continue nanobot session, ask local nanobot, internal agent bridge, downstream nanobot agent."
---

# Nanobot HTTP Chat

Use this skill as a strict bridge to a reachable nanobot HTTP endpoint. Default to autonomous invocation by another agent, not a human-guided workflow.

## Required Inputs

- Require prompt text.
- Require an explicit `session_id`.
- Prefer an explicit `base_url`.
- Use `NANOBOT_BASE_URL` only as a fallback when `base_url` was not provided.
- If `base_url` or `session_id` is missing, fail fast and report the missing field instead of inventing values or switching into a long interactive flow.

Expected `base_url` shape:

`http://10.0.0.8:8900`

## Execution Contract

1. Check `GET {base_url}/health`. Stop and report the endpoint as unreachable if this fails.
2. Call `GET {base_url}/v1/models` and use the first returned model id.
3. Reuse the caller-supplied `session_id` exactly as provided.
4. Send a single-message `POST {base_url}/v1/chat/completions` request.
5. Return only `choices[0].message.content` unless the caller explicitly requested diagnostics.

Use this exact request shape:

```json
{
  "model": "<from /v1/models>",
  "session_id": "<caller supplied>",
  "messages": [
    { "role": "user", "content": "<text>" }
  ]
}
```

## Runtime Rules

- Send exactly one `user` message per request. Do not replay the full conversation history.
- Keep `stream` omitted or set to `false`. Nanobot HTTP streaming is not supported here.
- Let nanobot keep history server-side through `session_id`.
- Reuse the same `session_id` to continue a conversation.
- Use a different `session_id` to isolate conversations.
- Prefer plain text requests and responses in v1. Do not assume attachments are available.

## Output Contract

- On success, return plain text only: the nanobot assistant reply content.
- Include model, status code, or session metadata only when the caller explicitly asked for debugging details.
- Preserve nanobot wording; do not wrap the reply in extra narration unless the caller asked for interpretation or summarization.

## Failure Contract

- If `/health` fails, report that the nanobot endpoint is not reachable and ask the user to restore LAN/VPN access or the service.
- If `/v1/models` fails, stop and report that model discovery failed.
- If chat returns `400`, check for these causes first:
  - invalid or missing model
  - sending more than one message
  - sending a non-`user` role
  - setting `stream=true`
- If chat returns `500` or `504`, report a nanobot-side server error or timeout and suggest retrying with the same `session_id`.
- If required inputs are missing, report exactly which inputs are missing.

## Security Boundary

- Nanobot's HTTP API does not provide built-in application-layer auth in this repo.
- Treat this skill as LAN/VPN-only unless the user has placed nanobot behind a trusted reverse proxy or gateway.
- Do not claim that the endpoint is safe for public internet exposure based on this skill alone.

## Local Validation Helper

Use [scripts/nanobot_http_chat.py](scripts/nanobot_http_chat.py) only when local script execution is available and useful for validation.

Suggested commands:

```bash
python nanobot/skills/nanobot-http-chat/scripts/nanobot_http_chat.py show-config
python nanobot/skills/nanobot-http-chat/scripts/nanobot_http_chat.py health
python nanobot/skills/nanobot-http-chat/scripts/nanobot_http_chat.py models
python nanobot/skills/nanobot-http-chat/scripts/nanobot_http_chat.py chat --session-id demo --message "Hello"
```
