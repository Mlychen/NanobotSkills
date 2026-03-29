from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from models import (  # noqa: E402
    ProjectTurnInput,
    RawEventRecord,
    ThreadContent,
    ThreadEventRef,
    ThreadFactTime,
    ThreadMeta,
    ThreadPlanTime,
    ThreadRecord,
)
from store import TimelineStore  # noqa: E402
logging.basicConfig(level=logging.WARNING)

TIMELINE_META_KEY = "_timeline_memory"
DEFAULT_SOURCE = "skill://timeline-memory"
DEFAULT_USER_ACTOR_ID = "user"
DEFAULT_ASSISTANT_ACTOR_ID = "assistant"


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def read_input_json(path: str | None) -> dict:
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return json.load(sys.stdin)


def emit_json(payload: Any) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0


def derive_thread_id(turn_id: str) -> str:
    return f"thr_{turn_id.encode('utf-8').hex()}"


def resolve_effective_source(turn_input: ProjectTurnInput) -> str:
    return turn_input.context.source or DEFAULT_SOURCE


def resolve_thread_id(turn_input: ProjectTurnInput) -> str | None:
    if turn_input.thread is None:
        return None
    return turn_input.thread.thread_id or derive_thread_id(turn_input.turn_id)


def resolve_replay_thread_ids(turn_input: ProjectTurnInput) -> set[str | None]:
    if turn_input.thread is None:
        return {None}
    return {resolve_thread_id(turn_input)}


def build_event_id(turn_id: str, role: str) -> str:
    suffix = "in" if role == "inbound" else "out"
    return f"{turn_id}:{suffix}"


def build_timeline_meta(
    *,
    turn_input: ProjectTurnInput,
    role: str,
    fingerprint: str,
    thread_id: str | None,
) -> dict[str, Any]:
    return {
        "turn_id": turn_input.turn_id,
        "role": role,
        "fingerprint": fingerprint,
        "thread_id": thread_id,
    }


def extract_timeline_meta(record: RawEventRecord) -> dict[str, Any]:
    payload = record.payload if isinstance(record.payload, dict) else {}
    meta = payload.get(TIMELINE_META_KEY)
    if not isinstance(meta, dict):
        raise ValueError(f"raw event {record.event_id} is missing {TIMELINE_META_KEY} metadata")
    return dict(meta)


def build_event_payload(
    *,
    text: str,
    role: str,
    turn_input: ProjectTurnInput,
    fingerprint: str,
    thread_id: str | None,
) -> dict[str, Any]:
    return {
        "message": text,
        TIMELINE_META_KEY: build_timeline_meta(
            turn_input=turn_input,
            role=role,
            fingerprint=fingerprint,
            thread_id=thread_id,
        ),
    }


def merge_event_refs(
    current: ThreadRecord | None,
    *,
    event_ids: list[str],
    recorded_at: str,
    source: str,
) -> list[ThreadEventRef]:
    merged: list[ThreadEventRef] = []
    if current is not None:
        merged.extend(current.event_refs)
    for index, event_id in enumerate(event_ids):
        merged.append(
            ThreadEventRef(
                event_id=event_id,
                role="primary" if index == 0 else "context",
                added_at=recorded_at,
                added_by=source,
            )
        )
    return merged

def build_thread_record(
    *,
    turn_input: ProjectTurnInput,
    thread_id: str,
    recorded_at: str,
    event_ids: list[str],
    current: ThreadRecord | None,
    source: str,
) -> ThreadRecord:
    thread_input = turn_input.thread
    if thread_input is None:
        raise ValueError("thread input is required")
    return ThreadRecord(
        thread_id=thread_id,
        thread_kind=thread_input.thread_kind,
        title=thread_input.title,
        status=thread_input.status,
        plan_time=ThreadPlanTime.from_dict(thread_input.plan_time.to_dict()),
        fact_time=ThreadFactTime.from_dict(thread_input.fact_time.to_dict()),
        content=ThreadContent.from_dict(thread_input.content.to_dict()),
        event_refs=merge_event_refs(
            current,
            event_ids=event_ids,
            recorded_at=recorded_at,
            source=source,
        ),
        meta=ThreadMeta(
            created_by=current.meta.created_by if current is not None else source,
            updated_by=source,
        ),
        first_event_at=current.first_event_at if current is not None else recorded_at,
        last_event_at=recorded_at,
        created_at=current.created_at if current is not None else recorded_at,
        updated_at=recorded_at,
    )


def ensure_replay_metadata_matches(
    *,
    turn_input: ProjectTurnInput,
    record: RawEventRecord,
    fingerprint: str,
    role: str,
) -> str | None:
    meta = extract_timeline_meta(record)
    if meta.get("turn_id") != turn_input.turn_id:
        raise ValueError(f"turn_id conflict: raw event {record.event_id} does not belong to {turn_input.turn_id}")
    if meta.get("fingerprint") != fingerprint:
        raise ValueError(f"turn_id conflict: different payload already recorded for {turn_input.turn_id}")
    if meta.get("role") != role:
        raise ValueError(f"turn_id conflict: raw event {record.event_id} has unexpected role metadata")
    thread_id = meta.get("thread_id")
    return str(thread_id) if thread_id is not None else None


def build_raw_event(
    *,
    turn_input: ProjectTurnInput,
    role: str,
    recorded_at: str,
    fingerprint: str,
    thread_id: str | None,
    source: str,
) -> RawEventRecord:
    if role == "inbound":
        return RawEventRecord(
            event_id=build_event_id(turn_input.turn_id, "inbound"),
            event_type="user_message",
            recorded_at=recorded_at,
            source=source,
            actor_kind="user",
            actor_id=turn_input.context.actor_id or DEFAULT_USER_ACTOR_ID,
            raw_text=turn_input.user_text,
            payload=build_event_payload(
                text=turn_input.user_text,
                role="inbound",
                turn_input=turn_input,
                fingerprint=fingerprint,
                thread_id=thread_id,
            ),
            schema_version=1,
        )
    if turn_input.assistant_text is None:
        raise ValueError("assistant_text is required for outbound events")
    return RawEventRecord(
        event_id=build_event_id(turn_input.turn_id, "outbound"),
        event_type="assistant_response",
        recorded_at=recorded_at,
        source=source,
        actor_kind="assistant",
        actor_id=turn_input.context.assistant_actor_id or DEFAULT_ASSISTANT_ACTOR_ID,
        raw_text=turn_input.assistant_text,
        payload=build_event_payload(
            text=turn_input.assistant_text,
            role="outbound",
            turn_input=turn_input,
            fingerprint=fingerprint,
            thread_id=thread_id,
        ),
        schema_version=1,
    )


def replay_result(store: TimelineStore, turn_input: ProjectTurnInput) -> dict[str, Any] | None:
    fingerprint = turn_input.fingerprint()
    expected_thread_ids = resolve_replay_thread_ids(turn_input)
    inbound_id = build_event_id(turn_input.turn_id, "inbound")
    outbound_id = build_event_id(turn_input.turn_id, "outbound")
    inbound = store.get_raw_event(inbound_id)
    outbound = store.get_raw_event(outbound_id)

    if inbound is None and outbound is None:
        return None
    if inbound is None:
        raise ValueError(f"turn_id conflict: partial write detected for {turn_input.turn_id} (missing inbound)")
    if turn_input.assistant_text is None and outbound is not None:
        raise ValueError(f"turn_id conflict: partial write detected for {turn_input.turn_id} (unexpected outbound)")

    thread_id = ensure_replay_metadata_matches(
        turn_input=turn_input,
        record=inbound,
        fingerprint=fingerprint,
        role="inbound",
    )
    if thread_id not in expected_thread_ids:
        raise ValueError(f"turn_id conflict: inconsistent thread metadata for {turn_input.turn_id}")
    recorded_ids = [inbound.event_id]
    needs_repair = False
    if outbound is not None:
        outbound_thread_id = ensure_replay_metadata_matches(
            turn_input=turn_input,
            record=outbound,
            fingerprint=fingerprint,
            role="outbound",
        )
        if outbound_thread_id != thread_id:
            raise ValueError(f"turn_id conflict: inconsistent thread metadata for {turn_input.turn_id}")
        recorded_ids.append(outbound.event_id)
    elif turn_input.assistant_text is not None:
        needs_repair = True

    thread_payload = None
    current_thread = None
    if turn_input.thread is not None:
        if thread_id is None:
            raise ValueError(f"turn_id conflict: partial write detected for {turn_input.turn_id} (missing thread)")
        current_thread = store.get_thread(thread_id)
        if current_thread is None:
            needs_repair = True
        else:
            thread_payload = current_thread.to_dict()

    if needs_repair:
        if current_thread is not None and turn_input.assistant_text is not None and outbound is None:
            raise ValueError(
                f"turn_id conflict: partial write detected for {turn_input.turn_id} "
                f"(thread snapshot exists without outbound)"
            )
        return {
            "ok": True,
            "idempotent_replay": False,
            "recorded_event_ids": recorded_ids,
            "thread": thread_payload,
            "_recovery": {
                "recorded_at": inbound.recorded_at,
                "fingerprint": fingerprint,
                "thread_id": thread_id,
                "write_outbound": outbound is None and turn_input.assistant_text is not None,
                "write_thread": turn_input.thread is not None and current_thread is None,
                "current_thread": current_thread,
            },
        }

    return {
        "ok": True,
        "idempotent_replay": True,
        "recorded_event_ids": recorded_ids,
        "thread": thread_payload,
    }


def cmd_get_thread(args: argparse.Namespace) -> int:
    store = TimelineStore(Path(args.store_root))
    record = store.get_thread(args.thread_id)
    return emit_json(record.to_dict() if record else None)


def cmd_list_threads(args: argparse.Namespace) -> int:
    store = TimelineStore(Path(args.store_root))
    records = store.list_threads(thread_kind=args.thread_kind, status=args.status)
    return emit_json([record.to_dict() for record in records])


def cmd_list_thread_history(args: argparse.Namespace) -> int:
    store = TimelineStore(Path(args.store_root))
    records = store.list_thread_history(args.thread_id)
    return emit_json([record.to_dict() for record in records])


def cmd_project_turn(args: argparse.Namespace) -> int:
    store = TimelineStore(Path(args.store_root))
    turn_input = ProjectTurnInput.from_dict(read_input_json(args.input))
    effective_source = resolve_effective_source(turn_input)
    fingerprint = turn_input.fingerprint()
    thread_id = resolve_thread_id(turn_input)

    replay = replay_result(store, turn_input)
    if replay is not None:
        recovery = replay.pop("_recovery", None)
        if recovery is None:
            return emit_json(replay)

        recorded_at = recovery["recorded_at"]
        recorded_ids = list(replay["recorded_event_ids"])
        current_thread = recovery["current_thread"]
        recovery_thread_id = recovery["thread_id"]

        if recovery["write_outbound"]:
            outbound_event = build_raw_event(
                turn_input=turn_input,
                role="outbound",
                recorded_at=recorded_at,
                fingerprint=fingerprint,
                thread_id=recovery_thread_id,
                source=effective_source,
            )
            store.append_raw_event(outbound_event)
            recorded_ids.append(outbound_event.event_id)

        thread_payload = replay["thread"]
        if recovery["write_thread"]:
            thread_record = build_thread_record(
                turn_input=turn_input,
                thread_id=recovery_thread_id,
                recorded_at=recorded_at,
                event_ids=recorded_ids,
                current=None,
                source=effective_source,
            )
            thread_payload = store.upsert_thread(thread_record).to_dict()

        replay["recorded_event_ids"] = recorded_ids
        replay["thread"] = thread_payload
        return emit_json(replay)

    recorded_at = now_iso()
    inbound_event = build_raw_event(
        turn_input=turn_input,
        role="inbound",
        recorded_at=recorded_at,
        fingerprint=fingerprint,
        thread_id=thread_id,
        source=effective_source,
    )
    store.append_raw_event(inbound_event)
    recorded_ids = [inbound_event.event_id]

    if turn_input.assistant_text is not None:
        outbound_event = build_raw_event(
            turn_input=turn_input,
            role="outbound",
            recorded_at=recorded_at,
            fingerprint=fingerprint,
            thread_id=thread_id,
            source=effective_source,
        )
        store.append_raw_event(outbound_event)
        recorded_ids.append(outbound_event.event_id)

    thread_payload = None
    if thread_id is not None:
        current = store.get_thread(thread_id)
        thread_record = build_thread_record(
            turn_input=turn_input,
            thread_id=thread_id,
            recorded_at=recorded_at,
            event_ids=recorded_ids,
            current=current,
            source=effective_source,
        )
        thread_payload = store.upsert_thread(thread_record).to_dict()

    return emit_json(
        {
            "ok": True,
            "idempotent_replay": False,
            "recorded_event_ids": recorded_ids,
            "thread": thread_payload,
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Timeline memory CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    get_thread = subparsers.add_parser("get-thread")
    get_thread.add_argument("--store-root", required=True)
    get_thread.add_argument("--thread-id", required=True)
    get_thread.set_defaults(func=cmd_get_thread)

    list_threads = subparsers.add_parser("list-threads")
    list_threads.add_argument("--store-root", required=True)
    list_threads.add_argument("--thread-kind")
    list_threads.add_argument("--status")
    list_threads.set_defaults(func=cmd_list_threads)

    list_thread_history = subparsers.add_parser("list-thread-history")
    list_thread_history.add_argument("--store-root", required=True)
    list_thread_history.add_argument("--thread-id", required=True)
    list_thread_history.set_defaults(func=cmd_list_thread_history)

    project_turn = subparsers.add_parser("project-turn")
    project_turn.add_argument("--store-root", required=True)
    project_turn.add_argument("--input")
    project_turn.set_defaults(func=cmd_project_turn)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
