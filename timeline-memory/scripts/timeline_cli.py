from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from errors import (
    TimelineInvalidArgumentError,
    TimelineMetadataConflictError,
    TimelinePartialWriteError,
    TimelineReadFailedError,
    TimelineStructuredError,
    TimelineTurnConflictError,
)
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
from store import (  # noqa: E402
    DEFAULT_JSONL_READ_MODE,
    PROJECT_TURN_TXN_STAGE_ORDER,
    StoreWriteBusyError,
    TimelineStore,
    VALID_JSONL_READ_MODES,
    thread_listing_sort_key,
)
from time_utils import parse_optional_timestamp  # noqa: E402
logging.basicConfig(level=logging.WARNING)

TIMELINE_META_KEY = "_timeline_memory"
DEFAULT_SOURCE = "skill://timeline-memory"
DEFAULT_USER_ACTOR_ID = "user"
DEFAULT_ASSISTANT_ACTOR_ID = "assistant"
DEFAULT_LIST_THREADS_PAGE_SIZE = 100
MAX_LIST_THREADS_PAGE_SIZE = 200
JSONL_READ_ERROR_PATTERN = re.compile(r"^failed to read JSONL: (?P<path>.+) line (?P<line_no>\d+): (?P<reason>.+)$")
INPUT_JSON_ERROR_PATTERN = re.compile(
    r"^failed to parse input JSON: (?P<path>.+) line (?P<line_no>\d+) column (?P<column_no>\d+): (?P<reason>.+)$"
)
INPUT_JSON_READ_ERROR_PATTERN = re.compile(r"^failed to read input JSON: (?P<path>.+): (?P<reason>.+)$")


class TimelineCliError(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        category: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": {
                "code": self.code,
                "category": self.category,
                "message": self.message,
                "details": self.details,
            },
        }


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def read_input_json(path: str | None) -> dict:
    source = path or "<stdin>"
    try:
        if path:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        return json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        raise TimelineInvalidArgumentError(
            f"failed to parse input JSON: {source} line {exc.lineno} column {exc.colno}: {exc.msg}",
            details={
                "path": source,
                "line_no": exc.lineno,
                "column_no": exc.colno,
                "reason": exc.msg,
            },
        ) from exc
    except OSError as exc:
        reason = exc.strerror or str(exc) or exc.__class__.__name__
        raise TimelineInvalidArgumentError(
            f"failed to read input JSON: {source}: {reason}",
            details={"path": source, "reason": reason},
        ) from exc


def emit_json(payload: Any) -> int:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))
    sys.stdout.write("\n")
    return 0


def emit_error(error: TimelineCliError) -> int:
    sys.stderr.write(json.dumps(error.to_dict(), ensure_ascii=False, indent=2))
    sys.stderr.write("\n")
    return 1


def _parse_list_threads_limit(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        limit = int(raw)
    except ValueError as exc:
        raise TimelineInvalidArgumentError("list-threads limit must be a positive integer") from exc
    if limit <= 0:
        raise TimelineInvalidArgumentError("list-threads limit must be a positive integer")
    if limit > MAX_LIST_THREADS_PAGE_SIZE:
        raise TimelineInvalidArgumentError(f"list-threads limit must be <= {MAX_LIST_THREADS_PAGE_SIZE}")
    return limit


def _parse_list_threads_timestamp(raw: str | None, *, field_name: str) -> datetime | None:
    if raw is None:
        return None
    parsed = parse_optional_timestamp(raw, context=field_name, emit_warning=False)
    if parsed is None:
        raise TimelineInvalidArgumentError(f"list-threads {field_name} must be a valid ISO 8601 timestamp")
    return parsed


def _normalize_list_threads_filters(
    *,
    thread_kind: str | None,
    status: str | None,
    last_event_at_or_after: datetime | None,
    last_event_at_or_before: datetime | None,
) -> dict[str, str | None]:
    return {
        "thread_kind": thread_kind,
        "status": status,
        "last_event_at_or_after": last_event_at_or_after.isoformat() if last_event_at_or_after is not None else None,
        "last_event_at_or_before": last_event_at_or_before.isoformat() if last_event_at_or_before is not None else None,
    }


def _encode_list_threads_cursor(payload: dict[str, Any]) -> str:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return encoded.decode("ascii").rstrip("=")


def _decode_list_threads_cursor(raw: str) -> dict[str, Any]:
    try:
        padding = "=" * (-len(raw) % 4)
        decoded = base64.urlsafe_b64decode(f"{raw}{padding}".encode("ascii"))
        payload = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise TimelineInvalidArgumentError("list-threads cursor is invalid") from exc
    if not isinstance(payload, dict):
        raise TimelineInvalidArgumentError("list-threads cursor is invalid")
    return payload


def _thread_cursor_payload(record: ThreadRecord) -> dict[str, Any]:
    return {
        "last_event_at": record.last_event_at,
        "updated_at": record.updated_at,
        "thread_id": record.thread_id,
    }


def _require_valid_cursor_timestamp(raw: str | None, *, field_name: str, required: bool) -> str | None:
    if raw is None:
        if required:
            raise TimelineInvalidArgumentError("list-threads cursor is invalid")
        return None
    if not isinstance(raw, str) or not raw:
        raise TimelineInvalidArgumentError("list-threads cursor is invalid")
    if parse_optional_timestamp(
        raw,
        context=f"list-threads cursor {field_name}",
        emit_warning=False,
    ) is None:
        raise TimelineInvalidArgumentError("list-threads cursor is invalid")
    return raw


def _validate_list_threads_cursor(
    raw: str,
    *,
    filters: dict[str, str | None],
) -> tuple[tuple[tuple[bool, float], tuple[bool, float], str], dict[str, Any]]:
    payload = _decode_list_threads_cursor(raw)
    if payload.get("v") != 1:
        raise TimelineInvalidArgumentError("list-threads cursor is invalid")
    position = payload.get("position")
    if not isinstance(position, dict):
        raise TimelineInvalidArgumentError("list-threads cursor is invalid")
    thread_id = position.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id:
        raise TimelineInvalidArgumentError("list-threads cursor is invalid")
    updated_at = _require_valid_cursor_timestamp(
        position.get("updated_at"),
        field_name="updated_at",
        required=True,
    )
    last_event_at = _require_valid_cursor_timestamp(
        position.get("last_event_at"),
        field_name="last_event_at",
        required=False,
    )
    record = ThreadRecord(
        thread_id=thread_id,
        thread_kind="cursor",
        title="cursor",
        status="cursor",
        plan_time=ThreadPlanTime(),
        fact_time=ThreadFactTime(),
        content=ThreadContent(),
        event_refs=[],
        meta=ThreadMeta(created_by="cursor", updated_by="cursor"),
        first_event_at=None,
        last_event_at=last_event_at,
        created_at="1970-01-01T00:00:00+00:00",
        updated_at=updated_at,
    )
    cursor_filters = payload.get("filters")
    if cursor_filters != filters:
        raise TimelineInvalidArgumentError("list-threads cursor does not match current filters")
    return thread_listing_sort_key(record), payload


def _paginate_list_threads(
    records: list[ThreadRecord],
    *,
    limit: int,
    cursor: str | None,
    filters: dict[str, str | None],
) -> dict[str, Any]:
    filtered_records = records
    if cursor is not None:
        cursor_key, _ = _validate_list_threads_cursor(cursor, filters=filters)
        filtered_records = [record for record in records if thread_listing_sort_key(record) < cursor_key]
    items = filtered_records[:limit]
    has_more = len(filtered_records) > limit
    next_cursor = None
    if has_more and items:
        next_cursor = _encode_list_threads_cursor(
            {
                "v": 1,
                "filters": filters,
                "position": _thread_cursor_payload(items[-1]),
            }
        )
    return {
        "items": [record.to_dict() for record in items],
        "next_cursor": next_cursor,
        "has_more": has_more,
    }


def build_store(args: argparse.Namespace) -> TimelineStore:
    return TimelineStore(Path(args.store_root), read_mode=args.read_mode)


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


def required_turn_event_ids(turn_input: ProjectTurnInput) -> list[str]:
    event_ids = [build_event_id(turn_input.turn_id, "inbound")]
    if turn_input.assistant_text is not None:
        event_ids.append(build_event_id(turn_input.turn_id, "outbound"))
    return event_ids


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
        raise TimelineMetadataConflictError(f"raw event {record.event_id} is missing {TIMELINE_META_KEY} metadata")
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
                role=project_turn_event_ref_role(index),
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
        raise TimelineInvalidArgumentError("thread input is required")
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


def thread_ref_event_ids(record: ThreadRecord | None) -> set[str]:
    if record is None:
        return set()
    return {ref.event_id for ref in record.event_refs}


def has_contiguous_thread_history(
    store: TimelineStore,
    *,
    thread_id: str,
    current_thread: ThreadRecord,
) -> bool:
    current_revision = current_thread.meta.revision
    if current_revision <= 1:
        return True
    latest_history = store.latest_thread_history(thread_id)
    if latest_history is None:
        return False
    return latest_history.meta.revision == current_revision - 1


@dataclass
class ReplayRawState:
    recorded_at: str
    required_event_ids: list[str]
    recorded_event_ids: list[str]
    thread_id: str | None
    raw_complete: bool
    needs_repair: bool


@dataclass
class ReplayThreadState:
    thread: dict[str, Any] | None
    current_thread: ThreadRecord | None
    baseline_thread: ThreadRecord | None
    append_history: bool
    needs_repair: bool


@dataclass
class ThreadWritePlan:
    target_thread: ThreadRecord
    history_entry: ThreadRecord | None


@dataclass
class ReplayRecoveryPlan:
    recorded_at: str
    thread_id: str | None
    write_outbound: bool
    baseline_thread: ThreadRecord | None
    append_history: bool
    thread_action: str


@dataclass
class ReplayResult:
    idempotent_replay: bool
    recorded_event_ids: list[str]
    thread: dict[str, Any] | None
    recovery: ReplayRecoveryPlan | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": True,
            "idempotent_replay": self.idempotent_replay,
            "recorded_event_ids": list(self.recorded_event_ids),
            "thread": self.thread,
        }


def resolve_replay_baseline(
    store: TimelineStore,
    *,
    thread_id: str,
) -> tuple[ThreadRecord | None, ThreadRecord | None, bool]:
    current_thread = store.get_thread(thread_id)
    if current_thread is not None:
        return current_thread, current_thread, True
    history_thread = store.latest_thread_history(thread_id)
    return None, history_thread, False


def ensure_replay_metadata_matches(
    *,
    turn_input: ProjectTurnInput,
    record: RawEventRecord,
    fingerprint: str,
    role: str,
) -> str | None:
    meta = extract_timeline_meta(record)
    if meta.get("turn_id") != turn_input.turn_id:
        raise TimelineMetadataConflictError(
            f"turn_id conflict: raw event {record.event_id} does not belong to {turn_input.turn_id}",
            details={"turn_id": turn_input.turn_id},
        )
    if meta.get("fingerprint") != fingerprint:
        raise TimelineTurnConflictError(
            f"turn_id conflict: different payload already recorded for {turn_input.turn_id}",
            details={"turn_id": turn_input.turn_id},
        )
    if meta.get("role") != role:
        raise TimelineMetadataConflictError(
            f"turn_id conflict: raw event {record.event_id} has unexpected role metadata",
            details={"turn_id": turn_input.turn_id},
        )
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
            correlation_id=turn_input.turn_id,
            causation_id=None,
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
        raise TimelineInvalidArgumentError("assistant_text is required for outbound events")
    return RawEventRecord(
        event_id=build_event_id(turn_input.turn_id, "outbound"),
        event_type="assistant_response",
        recorded_at=recorded_at,
        source=source,
        actor_kind="assistant",
        actor_id=turn_input.context.assistant_actor_id or DEFAULT_ASSISTANT_ACTOR_ID,
        correlation_id=turn_input.turn_id,
        causation_id=build_event_id(turn_input.turn_id, "inbound"),
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


def project_turn_event_ref_role(index: int) -> str:
    return "primary" if index == 0 else "context"


def resolve_replay_raw_state(
    store: TimelineStore,
    *,
    turn_input: ProjectTurnInput,
    fingerprint: str,
) -> ReplayRawState | None:
    expected_thread_ids = resolve_replay_thread_ids(turn_input)
    required_event_ids = required_turn_event_ids(turn_input)
    inbound_id = required_event_ids[0]
    outbound_id = required_event_ids[1] if len(required_event_ids) > 1 else None
    inbound = store.get_raw_event(inbound_id)
    outbound = store.get_raw_event(outbound_id) if outbound_id is not None else None

    if inbound is None and outbound is None:
        return None
    if inbound is None:
        raise TimelinePartialWriteError(
            f"turn_id conflict: partial write detected for {turn_input.turn_id} (missing inbound)",
            details={"turn_id": turn_input.turn_id},
        )
    if turn_input.assistant_text is None and outbound is not None:
        raise TimelinePartialWriteError(
            f"turn_id conflict: partial write detected for {turn_input.turn_id} (unexpected outbound)",
            details={"turn_id": turn_input.turn_id},
        )

    thread_id = ensure_replay_metadata_matches(
        turn_input=turn_input,
        record=inbound,
        fingerprint=fingerprint,
        role="inbound",
    )
    if thread_id not in expected_thread_ids:
        raise TimelineMetadataConflictError(
            f"turn_id conflict: inconsistent thread metadata for {turn_input.turn_id}",
            details={"turn_id": turn_input.turn_id},
        )

    recorded_ids = [inbound.event_id]
    raw_complete = turn_input.assistant_text is None
    needs_repair = False
    if outbound is not None:
        outbound_thread_id = ensure_replay_metadata_matches(
            turn_input=turn_input,
            record=outbound,
            fingerprint=fingerprint,
            role="outbound",
        )
        if outbound_thread_id != thread_id:
            raise TimelineMetadataConflictError(
                f"turn_id conflict: inconsistent thread metadata for {turn_input.turn_id}",
                details={"turn_id": turn_input.turn_id},
            )
        recorded_ids.append(outbound.event_id)
        raw_complete = True
    elif turn_input.assistant_text is not None:
        needs_repair = True

    return ReplayRawState(
        recorded_at=inbound.recorded_at,
        required_event_ids=required_event_ids,
        recorded_event_ids=recorded_ids,
        thread_id=thread_id,
        raw_complete=raw_complete,
        needs_repair=needs_repair,
    )


def resolve_replay_thread_state(
    store: TimelineStore,
    *,
    turn_input: ProjectTurnInput,
    thread_id: str | None,
    required_event_ids: list[str],
    raw_complete: bool,
) -> ReplayThreadState:
    thread_payload = None
    current_thread = None
    baseline_thread = None
    append_history = False
    needs_repair = False

    if turn_input.thread is not None:
        if thread_id is None:
            raise TimelinePartialWriteError(
                f"turn_id conflict: partial write detected for {turn_input.turn_id} (missing thread)",
                details={"turn_id": turn_input.turn_id},
            )
        current_thread, baseline_thread, append_history = resolve_replay_baseline(store, thread_id=thread_id)
        baseline_ids = thread_ref_event_ids(baseline_thread)
        required_ids = set(required_event_ids)
        matched_ids = baseline_ids & required_ids
        if matched_ids and len(matched_ids) != len(required_ids):
            raise TimelinePartialWriteError(
                f"turn_id conflict: partial write detected for {turn_input.turn_id} "
                f"(thread snapshot partially reflects current turn)",
                details={"turn_id": turn_input.turn_id},
            )
        if len(matched_ids) == len(required_ids):
            if not raw_complete:
                raise TimelinePartialWriteError(
                    f"turn_id conflict: partial write detected for {turn_input.turn_id} "
                    f"(thread snapshot exists without complete raw events)",
                    details={"turn_id": turn_input.turn_id},
                )
            if current_thread is not None:
                if not has_contiguous_thread_history(store, thread_id=thread_id, current_thread=current_thread):
                    raise TimelinePartialWriteError(
                        f"turn_id conflict: partial write detected for {turn_input.turn_id} "
                        f"(thread snapshot exists without the previous revision in history)",
                        details={"turn_id": turn_input.turn_id, "thread_id": thread_id},
                    )
                thread_payload = current_thread.to_dict()
            else:
                thread_payload = baseline_thread.to_dict()
                needs_repair = True
        else:
            needs_repair = True

    return ReplayThreadState(
        thread=thread_payload,
        current_thread=current_thread,
        baseline_thread=baseline_thread,
        append_history=append_history,
        needs_repair=needs_repair,
    )


def replay_result(store: TimelineStore, turn_input: ProjectTurnInput) -> ReplayResult | None:
    fingerprint = turn_input.fingerprint()
    raw_state = resolve_replay_raw_state(store, turn_input=turn_input, fingerprint=fingerprint)
    if raw_state is None:
        return None
    thread_state = resolve_replay_thread_state(
        store,
        turn_input=turn_input,
        thread_id=raw_state.thread_id,
        required_event_ids=raw_state.required_event_ids,
        raw_complete=raw_state.raw_complete,
    )

    if raw_state.needs_repair or thread_state.needs_repair:
        return ReplayResult(
            idempotent_replay=False,
            recorded_event_ids=raw_state.recorded_event_ids,
            thread=thread_state.thread,
            recovery=ReplayRecoveryPlan(
                recorded_at=raw_state.recorded_at,
                thread_id=raw_state.thread_id,
                write_outbound=not raw_state.raw_complete and turn_input.assistant_text is not None,
                baseline_thread=thread_state.baseline_thread,
                append_history=thread_state.append_history,
                thread_action=resolve_replay_thread_action(turn_input=turn_input, thread_state=thread_state),
            ),
        )

    return ReplayResult(
        idempotent_replay=True,
        recorded_event_ids=raw_state.recorded_event_ids,
        thread=thread_state.thread,
    )


def resolve_replay_thread_action(
    *,
    turn_input: ProjectTurnInput,
    thread_state: ReplayThreadState,
) -> str:
    if turn_input.thread is None:
        return "none"
    if thread_state.current_thread is None and thread_state.baseline_thread is not None and bool(thread_state.thread):
        return "restore_snapshot"
    if thread_state.current_thread is None or thread_state.baseline_thread is not None:
        return "repair_thread"
    return "none"


def build_thread_write_plan(
    store: TimelineStore,
    *,
    turn_input: ProjectTurnInput,
    thread_id: str,
    recorded_at: str,
    event_ids: list[str],
    baseline_thread: ThreadRecord | None,
    source: str,
) -> ThreadWritePlan:
    target_thread = store.normalize_thread_for_write(
        build_thread_record(
            turn_input=turn_input,
            thread_id=thread_id,
            recorded_at=recorded_at,
            event_ids=event_ids,
            current=baseline_thread,
            source=source,
        ),
        current=baseline_thread,
    )
    return ThreadWritePlan(target_thread=target_thread, history_entry=baseline_thread)


def apply_replay_thread_write_plan(
    store: TimelineStore,
    *,
    plan: ThreadWritePlan,
) -> ThreadRecord:
    if plan.history_entry is not None:
        store.append_thread_history(plan.history_entry)
    record = store.write_thread_snapshot(plan.target_thread)
    return record


def _require_txn_str(txn: dict[str, Any], field_name: str) -> str:
    value = txn.get(field_name)
    if not isinstance(value, str) or not value:
        raise TimelineReadFailedError(f"project-turn txn.{field_name} must be a non-empty string")
    return value


def _require_txn_stage(txn: dict[str, Any]) -> str:
    stage = _require_txn_str(txn, "stage")
    if stage not in PROJECT_TURN_TXN_STAGE_ORDER:
        allowed = ", ".join(sorted(PROJECT_TURN_TXN_STAGE_ORDER))
        raise TimelineReadFailedError(f"project-turn txn.stage must be one of: {allowed}")
    return stage


def _require_txn_str_list(txn: dict[str, Any], field_name: str) -> list[str]:
    value = txn.get(field_name)
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise TimelineReadFailedError(f"project-turn txn.{field_name} must be a list of non-empty strings")
    return list(value)


def _load_txn_thread_record(txn: dict[str, Any], field_name: str) -> ThreadRecord | None:
    value = txn.get(field_name)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TimelineReadFailedError(f"project-turn txn.{field_name} must be a JSON object")
    try:
        return ThreadRecord.from_dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise TimelineReadFailedError(f"failed to load project-turn txn.{field_name}") from exc


def _thread_payload(record: ThreadRecord | None) -> dict[str, Any] | None:
    return record.to_dict() if record is not None else None


def _project_turn_result(
    *,
    recorded_event_ids: list[str],
    thread_record: ThreadRecord | None,
    idempotent_replay: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "idempotent_replay": idempotent_replay,
        "recorded_event_ids": recorded_event_ids,
        "thread": _thread_payload(thread_record),
    }


def build_raw_events_batch(
    *,
    turn_input: ProjectTurnInput,
    recorded_at: str,
    fingerprint: str,
    thread_id: str | None,
    source: str,
) -> list[RawEventRecord]:
    records = [
        build_raw_event(
            turn_input=turn_input,
            role="inbound",
            recorded_at=recorded_at,
            fingerprint=fingerprint,
            thread_id=thread_id,
            source=source,
        )
    ]
    if turn_input.assistant_text is not None:
        records.append(
            build_raw_event(
                turn_input=turn_input,
                role="outbound",
                recorded_at=recorded_at,
                fingerprint=fingerprint,
                thread_id=thread_id,
                source=source,
            )
        )
    return records


def commit_project_turn_raw_events(
    store: TimelineStore,
    *,
    turn_input: ProjectTurnInput,
    recorded_at: str,
    fingerprint: str,
    thread_id: str | None,
    source: str,
) -> list[str]:
    raw_records = build_raw_events_batch(
        turn_input=turn_input,
        recorded_at=recorded_at,
        fingerprint=fingerprint,
        thread_id=thread_id,
        source=source,
    )
    store.append_raw_events_batch(raw_records)
    return [record.event_id for record in raw_records]


def prepare_project_turn_txn(
    store: TimelineStore,
    *,
    turn_input: ProjectTurnInput,
    fingerprint: str,
    thread_id: str | None,
) -> dict[str, Any]:
    recorded_at = now_iso()
    baseline_thread = store.get_thread(thread_id) if thread_id is not None else None
    txn_payload = {
        "turn_id": turn_input.turn_id,
        "fingerprint": fingerprint,
        "stage": "prepared",
        "recorded_at": recorded_at,
        "thread_id": thread_id,
        "required_event_ids": required_turn_event_ids(turn_input),
        "has_thread": turn_input.thread is not None,
        "baseline_thread": _thread_payload(baseline_thread),
        "target_snapshot": None,
        "history_entry": None,
    }
    return store.write_project_turn_txn(turn_input.turn_id, txn_payload)


def advance_project_turn_txn(
    store: TimelineStore,
    turn_id: str,
    txn: dict[str, Any],
    *,
    stage: str | None = None,
    **updates: Any,
) -> dict[str, Any]:
    payload = dict(txn)
    if stage is not None:
        payload["stage"] = stage
    payload.update(updates)
    return store.write_project_turn_txn(turn_id, payload)


def ensure_project_turn_txn_matches(
    *,
    turn_input: ProjectTurnInput,
    txn: dict[str, Any],
    fingerprint: str,
    thread_id: str | None,
) -> list[str]:
    if txn.get("fingerprint") != fingerprint:
        raise TimelineTurnConflictError(
            f"turn_id conflict: different payload already recorded for {turn_input.turn_id}",
            details={"turn_id": turn_input.turn_id},
        )
    if txn.get("thread_id") != thread_id:
        raise TimelineMetadataConflictError(
            f"turn_id conflict: inconsistent thread metadata for {turn_input.turn_id}",
            details={"turn_id": turn_input.turn_id},
        )
    if bool(txn.get("has_thread")) != (turn_input.thread is not None):
        raise TimelineMetadataConflictError(
            f"turn_id conflict: inconsistent thread metadata for {turn_input.turn_id}",
            details={"turn_id": turn_input.turn_id},
        )
    recorded_event_ids = _require_txn_str_list(txn, "required_event_ids")
    if recorded_event_ids != required_turn_event_ids(turn_input):
        raise TimelineMetadataConflictError(
            f"turn_id conflict: inconsistent required event ids for {turn_input.turn_id}",
            details={"turn_id": turn_input.turn_id},
        )
    return recorded_event_ids


def execute_project_turn_txn(
    store: TimelineStore,
    *,
    turn_input: ProjectTurnInput,
    effective_source: str,
    fingerprint: str,
    thread_id: str | None,
    txn: dict[str, Any],
) -> dict[str, Any]:
    recorded_event_ids = ensure_project_turn_txn_matches(
        turn_input=turn_input,
        txn=txn,
        fingerprint=fingerprint,
        thread_id=thread_id,
    )
    stage = _require_txn_stage(txn)
    recorded_at = _require_txn_str(txn, "recorded_at")

    if stage == "committed":
        target_thread = _load_txn_thread_record(txn, "target_snapshot")
        if target_thread is None and thread_id is not None:
            target_thread = store.get_thread(thread_id)
        store.delete_project_turn_txn(turn_input.turn_id)
        return _project_turn_result(
            recorded_event_ids=recorded_event_ids,
            thread_record=target_thread,
            idempotent_replay=True,
        )

    if PROJECT_TURN_TXN_STAGE_ORDER[stage] < PROJECT_TURN_TXN_STAGE_ORDER["raw_committed"]:
        committed_event_ids = commit_project_turn_raw_events(
            store,
            turn_input=turn_input,
            recorded_at=recorded_at,
            fingerprint=fingerprint,
            thread_id=thread_id,
            source=effective_source,
        )
        if committed_event_ids != recorded_event_ids:
            raise TimelineMetadataConflictError(
                f"turn_id conflict: inconsistent raw event ids for {turn_input.turn_id}",
                details={"turn_id": turn_input.turn_id},
            )
        txn = advance_project_turn_txn(store, turn_input.turn_id, txn, stage="raw_committed")
        stage = "raw_committed"

    baseline_thread = _load_txn_thread_record(txn, "baseline_thread")
    target_thread = _load_txn_thread_record(txn, "target_snapshot")
    history_entry = _load_txn_thread_record(txn, "history_entry")
    if thread_id is not None and target_thread is None:
        thread_plan = build_thread_write_plan(
            store,
            turn_input=turn_input,
            thread_id=thread_id,
            recorded_at=recorded_at,
            event_ids=recorded_event_ids,
            baseline_thread=baseline_thread,
            source=effective_source,
        )
        target_thread = thread_plan.target_thread
        history_entry = thread_plan.history_entry
        txn = advance_project_turn_txn(
            store,
            turn_input.turn_id,
            txn,
            target_snapshot=target_thread.to_dict(),
            history_entry=_thread_payload(history_entry),
        )
        stage = _require_txn_stage(txn)

    if thread_id is not None and PROJECT_TURN_TXN_STAGE_ORDER[stage] < PROJECT_TURN_TXN_STAGE_ORDER["snapshot_committed"]:
        if target_thread is None:
            raise TimelinePartialWriteError(
                f"project-turn txn.target_snapshot is missing for {turn_input.turn_id}",
                details={"turn_id": turn_input.turn_id},
            )
        store.write_thread_snapshot(target_thread)
        txn = advance_project_turn_txn(store, turn_input.turn_id, txn, stage="snapshot_committed")
        stage = "snapshot_committed"

    if thread_id is not None and PROJECT_TURN_TXN_STAGE_ORDER[stage] < PROJECT_TURN_TXN_STAGE_ORDER["history_committed"]:
        if history_entry is not None:
            store.append_thread_history(history_entry)
        txn = advance_project_turn_txn(store, turn_input.turn_id, txn, stage="history_committed")
        stage = "history_committed"

    if PROJECT_TURN_TXN_STAGE_ORDER[stage] < PROJECT_TURN_TXN_STAGE_ORDER["committed"]:
        txn = advance_project_turn_txn(store, turn_input.turn_id, txn, stage="committed")
        target_thread = _load_txn_thread_record(txn, "target_snapshot") or target_thread

    store.delete_project_turn_txn(turn_input.turn_id)
    if thread_id is not None and target_thread is None:
        target_thread = store.get_thread(thread_id)
    return _project_turn_result(
        recorded_event_ids=recorded_event_ids,
        thread_record=target_thread,
        idempotent_replay=False,
    )


def execute_replay_recovery(
    store: TimelineStore,
    *,
    turn_input: ProjectTurnInput,
    replay: ReplayResult,
    effective_source: str,
    fingerprint: str,
) -> dict[str, Any]:
    recovery = replay.recovery
    if recovery is None:
        return replay.to_payload()

    replay.recorded_event_ids = recover_replay_raw_events(
        store,
        turn_input=turn_input,
        recovery=recovery,
        effective_source=effective_source,
        fingerprint=fingerprint,
        recorded_event_ids=replay.recorded_event_ids,
    )
    replay.thread = recover_replay_thread_payload(
        store,
        turn_input=turn_input,
        recovery=recovery,
        effective_source=effective_source,
        recorded_event_ids=replay.recorded_event_ids,
        current_thread_payload=replay.thread,
    )
    replay.recovery = None
    return replay.to_payload()


def recover_replay_raw_events(
    store: TimelineStore,
    *,
    turn_input: ProjectTurnInput,
    recovery: ReplayRecoveryPlan,
    effective_source: str,
    fingerprint: str,
    recorded_event_ids: list[str],
) -> list[str]:
    recovered_ids = list(recorded_event_ids)
    if not recovery.write_outbound:
        return recovered_ids
    return commit_project_turn_raw_events(
        store,
        turn_input=turn_input,
        recorded_at=recovery.recorded_at,
        fingerprint=fingerprint,
        thread_id=recovery.thread_id,
        source=effective_source,
    )


def recover_replay_thread_payload(
    store: TimelineStore,
    *,
    turn_input: ProjectTurnInput,
    recovery: ReplayRecoveryPlan,
    effective_source: str,
    recorded_event_ids: list[str],
    current_thread_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if recovery.thread_action == "none":
        return current_thread_payload
    if recovery.thread_action == "restore_snapshot":
        baseline_thread = recovery.baseline_thread
        if baseline_thread is None:
            raise TimelinePartialWriteError("replay recovery baseline thread is required for restore_snapshot")
        return store.write_thread_snapshot(baseline_thread).to_dict()
    if recovery.thread_action == "repair_thread":
        if recovery.thread_id is None:
            raise TimelinePartialWriteError("replay recovery thread_id is required for repair_thread")
        thread_plan = build_thread_write_plan(
            store,
            turn_input=turn_input,
            thread_id=recovery.thread_id,
            recorded_at=recovery.recorded_at,
            event_ids=recorded_event_ids,
            baseline_thread=recovery.baseline_thread,
            source=effective_source,
        )
        if not recovery.append_history:
            thread_plan = ThreadWritePlan(target_thread=thread_plan.target_thread, history_entry=None)
        return apply_replay_thread_write_plan(store, plan=thread_plan).to_dict()
    raise ValueError(f"unsupported replay recovery thread action: {recovery.thread_action}")


def recover_or_replay_project_turn(
    store: TimelineStore,
    *,
    turn_input: ProjectTurnInput,
    effective_source: str,
    fingerprint: str,
    thread_id: str | None,
) -> dict[str, Any] | None:
    txn = store.get_project_turn_txn(turn_input.turn_id)
    if txn is not None:
        return execute_project_turn_txn(
            store,
            turn_input=turn_input,
            effective_source=effective_source,
            fingerprint=fingerprint,
            thread_id=thread_id,
            txn=txn,
        )

    replay = replay_result(store, turn_input)
    if replay is None:
        return None

    return execute_replay_recovery(
        store,
        turn_input=turn_input,
        replay=replay,
        effective_source=effective_source,
        fingerprint=fingerprint,
    )


def cmd_get_thread(args: argparse.Namespace) -> int:
    store = build_store(args)
    record = store.get_thread(args.thread_id)
    return emit_json(record.to_dict() if record else None)


def cmd_list_threads(args: argparse.Namespace) -> int:
    store = build_store(args)
    pagination_mode = args.limit is not None or args.cursor is not None
    limit = _parse_list_threads_limit(args.limit)
    last_event_at_or_after = _parse_list_threads_timestamp(
        args.last_event_at_or_after,
        field_name="last_event_at_or_after",
    )
    last_event_at_or_before = _parse_list_threads_timestamp(
        args.last_event_at_or_before,
        field_name="last_event_at_or_before",
    )
    if (
        last_event_at_or_after is not None
        and last_event_at_or_before is not None
        and last_event_at_or_after > last_event_at_or_before
    ):
        raise TimelineInvalidArgumentError("list-threads last_event_at_or_after must be <= last_event_at_or_before")
    records = store.list_threads(
        thread_kind=args.thread_kind,
        status=args.status,
        last_event_at_or_after=last_event_at_or_after,
        last_event_at_or_before=last_event_at_or_before,
    )
    if not pagination_mode:
        return emit_json([record.to_dict() for record in records])
    filters = _normalize_list_threads_filters(
        thread_kind=args.thread_kind,
        status=args.status,
        last_event_at_or_after=last_event_at_or_after,
        last_event_at_or_before=last_event_at_or_before,
    )
    page = _paginate_list_threads(
        records,
        limit=limit or DEFAULT_LIST_THREADS_PAGE_SIZE,
        cursor=args.cursor,
        filters=filters,
    )
    return emit_json(page)


def cmd_list_thread_history(args: argparse.Namespace) -> int:
    store = build_store(args)
    records = store.list_thread_history(args.thread_id)
    return emit_json([record.to_dict() for record in records])


def cmd_project_turn(args: argparse.Namespace) -> int:
    store = build_store(args)
    try:
        turn_input = ProjectTurnInput.from_dict(read_input_json(args.input))
    except TimelineStructuredError:
        raise
    except ValueError as exc:
        raise TimelineInvalidArgumentError(str(exc)) from exc
    effective_source = resolve_effective_source(turn_input)
    fingerprint = turn_input.fingerprint()
    thread_id = resolve_thread_id(turn_input)

    with store.project_turn_write_lock(turn_id=turn_input.turn_id, thread_id=thread_id):
        hold_seconds_raw = os.environ.get("TIMELINE_TEST_PROJECT_TURN_LOCK_HOLD_SECONDS")
        if hold_seconds_raw is not None and hold_seconds_raw.strip():
            hold_seconds = float(hold_seconds_raw)
            if hold_seconds > 0:
                time.sleep(hold_seconds)

        recovered = recover_or_replay_project_turn(
            store,
            turn_input=turn_input,
            effective_source=effective_source,
            fingerprint=fingerprint,
            thread_id=thread_id,
        )
        if recovered is not None:
            return emit_json(recovered)

        txn = prepare_project_turn_txn(
            store,
            turn_input=turn_input,
            fingerprint=fingerprint,
            thread_id=thread_id,
        )
        return emit_json(
            execute_project_turn_txn(
                store,
                turn_input=turn_input,
                effective_source=effective_source,
                fingerprint=fingerprint,
                thread_id=thread_id,
                txn=txn,
            )
        )


def _extract_turn_id(message: str) -> str | None:
    patterns = (
        r"for (?P<turn_id>[^ )]+)",
        r"does not belong to (?P<turn_id>[^ )]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, message)
        if match is not None:
            return match.group("turn_id")
    return None


def _details_with_turn_id(message: str) -> dict[str, Any]:
    turn_id = _extract_turn_id(message)
    return {"turn_id": turn_id} if turn_id is not None else {}


def _read_failed_details(message: str) -> dict[str, Any]:
    details: dict[str, Any] = {}
    match = JSONL_READ_ERROR_PATTERN.match(message)
    if match is not None:
        details["path"] = match.group("path")
        details["line_no"] = int(match.group("line_no"))
        details["reason"] = match.group("reason")
    return details


def _invalid_argument_details(message: str) -> dict[str, Any]:
    match = INPUT_JSON_ERROR_PATTERN.match(message)
    if match is not None:
        return {
            "path": match.group("path"),
            "line_no": int(match.group("line_no")),
            "column_no": int(match.group("column_no")),
            "reason": match.group("reason"),
        }
    match = INPUT_JSON_READ_ERROR_PATTERN.match(message)
    if match is not None:
        return {
            "path": match.group("path"),
            "reason": match.group("reason"),
        }
    return {}


def _is_partial_write_message(message: str) -> bool:
    snippets = (
        "partial write detected",
        "project-turn txn.target_snapshot is missing",
        "replay recovery baseline thread is required",
        "replay recovery thread_id is required",
    )
    return any(snippet in message for snippet in snippets)


def _is_metadata_conflict_message(message: str) -> bool:
    snippets = (
        "missing _timeline_memory metadata",
        "inconsistent thread metadata",
        "inconsistent required event ids",
        "inconsistent raw event ids",
        "unexpected role metadata",
        "does not belong to",
    )
    return any(snippet in message for snippet in snippets)


def _is_turn_conflict_message(message: str) -> bool:
    snippets = (
        "different payload already recorded",
        "raw event conflict:",
        "project-turn txn conflict:",
    )
    return any(snippet in message for snippet in snippets)


def _is_read_failed_message(message: str) -> bool:
    prefixes = (
        "failed to read JSONL:",
        "failed to load raw event:",
        "failed to load project-turn txn",
        "failed to load thread history:",
        "failed to load thread snapshot:",
        "thread history path mismatch:",
        "thread snapshot path mismatch:",
        "snapshot temp file",
    )
    return message.startswith(prefixes)


def _is_invalid_argument_message(message: str) -> bool:
    prefixes = (
        "failed to parse input JSON:",
        "failed to read input JSON:",
        "unsupported read mode:",
        "list-threads ",
    )
    snippets = (
        "contains unsupported fields:",
        "must be a mapping",
        "must be a JSON object",
        "must be a list",
        "must be a string",
        "must be a boolean",
        "must be namespaced",
        "is required",
        "must not be empty",
        "must be one of",
        "must contain only non-empty strings",
        "must be a non-negative number",
        "raw event payload must not contain plan_time or fact_time",
        "assistant_text is required for outbound events",
    )
    return message.startswith(prefixes) or any(snippet in message for snippet in snippets)


def classify_cli_error(exc: Exception) -> TimelineCliError:
    if isinstance(exc, TimelineCliError):
        return exc

    if isinstance(exc, TimelineStructuredError):
        return TimelineCliError(
            code=exc.code,
            category=exc.category,
            message=str(exc) or exc.__class__.__name__,
            details=dict(exc.details),
        )

    message = str(exc) or exc.__class__.__name__

    if isinstance(exc, StoreWriteBusyError):
        details: dict[str, Any] = {
            "retryable": True,
            "turn_id": exc.turn_id,
            "thread_id": exc.thread_id,
        }
        if exc.lock_path is not None:
            details["path"] = exc.lock_path
        return TimelineCliError(
            code="TM_STORE_BUSY",
            category="busy",
            message=message,
            details={key: value for key, value in details.items() if value is not None},
        )

    if _is_partial_write_message(message):
        return TimelineCliError(
            code="TM_PARTIAL_WRITE",
            category="recovery_failed",
            message=message,
            details=_details_with_turn_id(message),
        )

    if _is_metadata_conflict_message(message):
        return TimelineCliError(
            code="TM_METADATA_CONFLICT",
            category="conflict",
            message=message,
            details=_details_with_turn_id(message),
        )

    if _is_turn_conflict_message(message):
        return TimelineCliError(
            code="TM_TURN_CONFLICT",
            category="conflict",
            message=message,
            details=_details_with_turn_id(message),
        )

    if _is_read_failed_message(message):
        return TimelineCliError(
            code="TM_READ_FAILED",
            category="read_failed",
            message=message,
            details=_read_failed_details(message),
        )

    if _is_invalid_argument_message(message):
        return TimelineCliError(
            code="TM_INVALID_ARGUMENT",
            category="invalid_argument",
            message=message,
            details=_invalid_argument_details(message),
        )

    return TimelineCliError(
        code="TM_INTERNAL",
        category="internal",
        message=message,
        details={"exception_type": exc.__class__.__name__},
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Timeline memory CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_read_mode_argument(command_parser: argparse.ArgumentParser) -> None:
        command_parser.add_argument(
            "--read-mode",
            choices=sorted(VALID_JSONL_READ_MODES),
            default=DEFAULT_JSONL_READ_MODE,
        )

    get_thread = subparsers.add_parser("get-thread")
    get_thread.add_argument("--store-root", required=True)
    get_thread.add_argument("--thread-id", required=True)
    add_read_mode_argument(get_thread)
    get_thread.set_defaults(func=cmd_get_thread)

    list_threads = subparsers.add_parser("list-threads")
    list_threads.add_argument("--store-root", required=True)
    list_threads.add_argument("--thread-kind")
    list_threads.add_argument("--status")
    list_threads.add_argument("--limit")
    list_threads.add_argument("--cursor")
    list_threads.add_argument("--last-event-at-or-after")
    list_threads.add_argument("--last-event-at-or-before")
    add_read_mode_argument(list_threads)
    list_threads.set_defaults(func=cmd_list_threads)

    list_thread_history = subparsers.add_parser("list-thread-history")
    list_thread_history.add_argument("--store-root", required=True)
    list_thread_history.add_argument("--thread-id", required=True)
    add_read_mode_argument(list_thread_history)
    list_thread_history.set_defaults(func=cmd_list_thread_history)

    project_turn = subparsers.add_parser("project-turn")
    project_turn.add_argument("--store-root", required=True)
    project_turn.add_argument("--input")
    add_read_mode_argument(project_turn)
    project_turn.set_defaults(func=cmd_project_turn)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:
        return emit_error(classify_cli_error(exc))


if __name__ == "__main__":
    raise SystemExit(main())
