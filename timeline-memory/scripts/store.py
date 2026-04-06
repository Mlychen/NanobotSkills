from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from models import RawEventRecord, ThreadMeta, ThreadRecord
from time_utils import parse_optional_timestamp, timestamp_sort_key


logger = logging.getLogger(__name__)
THREAD_STORAGE_PREFIX = "tid_"
PROJECT_TURN_TXN_STORAGE_PREFIX = "turn_"
PROJECT_TURN_TXN_STAGES = {
    "prepared",
    "raw_committed",
    "snapshot_committed",
    "history_committed",
    "committed",
}
PROJECT_TURN_TXN_STAGE_ORDER = {
    "prepared": 0,
    "raw_committed": 1,
    "snapshot_committed": 2,
    "history_committed": 3,
    "committed": 4,
}
DEFAULT_JSONL_READ_MODE = "compat"
VALID_JSONL_READ_MODES = {"compat", "strict"}
DEFAULT_PROJECT_TURN_WRITE_LOCK_TIMEOUT_SECONDS = 5.0
DEFAULT_PROJECT_TURN_WRITE_LOCK_POLL_SECONDS = 0.05
PROJECT_TURN_WRITE_LOCK_TIMEOUT_ENV = "TIMELINE_PROJECT_TURN_WRITE_LOCK_TIMEOUT_SECONDS"
PROJECT_TURN_WRITE_LOCK_POLL_ENV = "TIMELINE_PROJECT_TURN_WRITE_LOCK_POLL_SECONDS"


class StoreWriteBusyError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        lock_path: Path | None = None,
        turn_id: str | None = None,
        thread_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.lock_path = str(lock_path) if lock_path is not None else None
        self.turn_id = turn_id
        self.thread_id = thread_id


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def encode_thread_storage_key(thread_id: str) -> str:
    encoded = thread_id.encode("utf-8").hex()
    return f"{THREAD_STORAGE_PREFIX}{encoded}"


def encode_project_turn_txn_storage_key(turn_id: str) -> str:
    encoded = turn_id.encode("utf-8").hex()
    return f"{PROJECT_TURN_TXN_STORAGE_PREFIX}{encoded}"


def is_thread_storage_path(path: Path) -> bool:
    return re.fullmatch(rf"{re.escape(THREAD_STORAGE_PREFIX)}[0-9a-f]+", path.stem) is not None


def thread_listing_sort_key(record: ThreadRecord) -> tuple[tuple[bool, float], tuple[bool, float], str]:
    return (
        timestamp_sort_key(record.last_event_at),
        timestamp_sort_key(record.updated_at),
        record.thread_id,
    )


def normalize_jsonl_read_mode(read_mode: str) -> str:
    normalized = read_mode.strip().lower()
    if normalized not in VALID_JSONL_READ_MODES:
        allowed = ", ".join(sorted(VALID_JSONL_READ_MODES))
        raise ValueError(f"unsupported read mode: {read_mode!r}; expected one of: {allowed}")
    return normalized


def _raise_jsonl_read_error(path: Path, *, line_no: int, reason: str, cause: Exception | None = None) -> None:
    message = f"failed to read JSONL: {path} line {line_no}: {reason}"
    if cause is None:
        raise ValueError(message)
    raise ValueError(message) from cause


def _require_mapping(payload: Any, name: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{name} must be a JSON object")
    return dict(payload)


def _temp_path_for(path: Path) -> Path:
    return path.parent / f"{path.name}.{uuid.uuid4().hex}.tmp"


def _open_lock_file(path: Path) -> BinaryIO:
    ensure_dir(path.parent)
    try:
        return open(path, "r+b")
    except FileNotFoundError:
        return open(path, "w+b")


def _try_lock_file(handle: BinaryIO) -> bool:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
        os.fsync(handle.fileno())
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock_file(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_lock_metadata(handle: BinaryIO, metadata: dict[str, Any]) -> None:
    encoded = json.dumps(metadata, ensure_ascii=False, indent=2).encode("utf-8")
    handle.seek(0)
    handle.truncate()
    handle.write(encoded)
    if not encoded.endswith(b"\n"):
        handle.write(b"\n")
    handle.flush()
    os.fsync(handle.fileno())


def _read_env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a non-negative number") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative number")
    return value


@contextmanager
def acquire_project_turn_write_lock(
    store_root: Path,
    *,
    turn_id: str,
    thread_id: str | None,
) -> Iterator[None]:
    timeout_seconds = _read_env_float(
        PROJECT_TURN_WRITE_LOCK_TIMEOUT_ENV,
        DEFAULT_PROJECT_TURN_WRITE_LOCK_TIMEOUT_SECONDS,
    )
    poll_seconds = _read_env_float(
        PROJECT_TURN_WRITE_LOCK_POLL_ENV,
        DEFAULT_PROJECT_TURN_WRITE_LOCK_POLL_SECONDS,
    )
    lock_path = ensure_dir(store_root / "_locks") / "project_turn.lock"
    deadline = time.monotonic() + timeout_seconds
    handle = _open_lock_file(lock_path)
    acquired = False
    try:
        while True:
            if _try_lock_file(handle):
                acquired = True
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise StoreWriteBusyError(
                    f"store is busy with another writer: {lock_path}",
                    lock_path=lock_path,
                    turn_id=turn_id,
                    thread_id=thread_id,
                )
            time.sleep(min(poll_seconds, remaining))
        _write_lock_metadata(
            handle,
            {
                "pid": os.getpid(),
                "turn_id": turn_id,
                "thread_id": thread_id,
                "acquired_at": _now_iso(),
            },
        )
        yield
    finally:
        try:
            if acquired:
                _unlock_file(handle)
        finally:
            handle.close()


def _write_text_atomic(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    temp_path = _temp_path_for(path)
    try:
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def _create_text_atomic(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    temp_path = _temp_path_for(path)
    try:
        temp_path.write_text(text, encoding="utf-8")
        temp_path.rename(path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise


def _require_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _require_str_list(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{name} must contain only non-empty strings")
    return list(value)


def _validate_project_turn_txn(turn_id: str, payload: Any) -> dict[str, Any]:
    normalized = _require_mapping(payload, "project-turn txn")
    payload_turn_id = _require_str(normalized.get("turn_id"), "project-turn txn.turn_id")
    if payload_turn_id != turn_id:
        raise ValueError(
            f"project-turn txn turn_id mismatch: expected {turn_id}, got {payload_turn_id}"
        )
    _require_str(normalized.get("fingerprint"), "project-turn txn.fingerprint")
    stage = _require_str(normalized.get("stage"), "project-turn txn.stage")
    if stage not in PROJECT_TURN_TXN_STAGES:
        allowed = ", ".join(sorted(PROJECT_TURN_TXN_STAGES))
        raise ValueError(f"project-turn txn.stage must be one of: {allowed}")
    if "required_event_ids" in normalized:
        normalized["required_event_ids"] = _require_str_list(
            normalized["required_event_ids"],
            "project-turn txn.required_event_ids",
        )
    return normalized


def _merge_project_turn_txn(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    current_stage = existing["stage"]
    next_stage = incoming["stage"]
    if PROJECT_TURN_TXN_STAGE_ORDER[next_stage] < PROJECT_TURN_TXN_STAGE_ORDER[current_stage]:
        raise ValueError(
            "project-turn txn stage rollback is not allowed: "
            f"{current_stage} -> {next_stage}"
        )
    merged = dict(existing)
    merged.update(incoming)
    return merged


def iter_jsonl(path: Path, *, read_mode: str = DEFAULT_JSONL_READ_MODE):
    normalized_mode = normalize_jsonl_read_mode(read_mode)
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                if normalized_mode == "strict":
                    _raise_jsonl_read_error(path, line_no=line_no, reason=f"malformed JSON ({exc.msg})", cause=exc)
                logger.warning("Skipping malformed JSONL line %s in %s: %s", line_no, path, exc)
                continue
            if not isinstance(payload, dict):
                if normalized_mode == "strict":
                    _raise_jsonl_read_error(path, line_no=line_no, reason="expected JSON object")
                logger.warning("Skipping non-object JSONL line %s in %s", line_no, path)
                continue
            yield payload

class RawEventStore:
    def __init__(self, store_root: Path, *, read_mode: str = DEFAULT_JSONL_READ_MODE):
        self.store_root = ensure_dir(store_root)
        self.path = self.store_root / "raw_events.jsonl"
        self.read_mode = normalize_jsonl_read_mode(read_mode)

    def append_raw_event(self, record: RawEventRecord) -> None:
        self.append_raw_events_batch([record])

    def append_raw_events_batch(self, records: list[RawEventRecord]) -> None:
        if not records:
            return
        seen: set[str] = set()
        duplicates: set[str] = set()
        for record in records:
            if record.event_id in seen:
                duplicates.add(record.event_id)
            seen.add(record.event_id)
        if duplicates:
            duplicate_id = sorted(duplicates)[0]
            raise ValueError(f"raw event batch contains duplicate event_id: {duplicate_id}")
        existing = self._existing_records(seen)
        for record in records:
            existing_record = existing.get(record.event_id)
            if existing_record is None:
                continue
            if existing_record.to_dict() != record.to_dict():
                raise ValueError(f"raw event conflict: different payload already exists: {record.event_id}")
        missing_records = [record for record in records if record.event_id not in existing]
        if not missing_records:
            return
        ensure_dir(self.path.parent)
        with open(self.path, "a", encoding="utf-8") as handle:
            for record in missing_records:
                handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def get_raw_event(self, event_id: str) -> RawEventRecord | None:
        if not self.path.exists():
            return None
        for payload in iter_jsonl(self.path, read_mode=self.read_mode):
            if payload.get("event_id") != event_id:
                continue
            try:
                return RawEventRecord.from_dict(payload)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"failed to load raw event: {event_id}") from exc
        return None

    def _existing_records(self, event_ids: set[str]) -> dict[str, RawEventRecord]:
        if not event_ids or not self.path.exists():
            return {}
        existing: dict[str, RawEventRecord] = {}
        for payload in iter_jsonl(self.path, read_mode=self.read_mode):
            event_id = payload.get("event_id")
            if not isinstance(event_id, str) or event_id not in event_ids:
                continue
            try:
                existing[event_id] = RawEventRecord.from_dict(payload)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"failed to load raw event: {event_id}") from exc
        return existing


class ProjectTurnTxnStore:
    def __init__(self, store_root: Path):
        self.txn_dir = ensure_dir(store_root / "_txn" / "project_turn")

    def path_for(self, turn_id: str) -> Path:
        return self.txn_dir / f"{encode_project_turn_txn_storage_key(turn_id)}.json"

    def get(self, turn_id: str) -> dict[str, Any] | None:
        path = self.path_for(turn_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"failed to load project-turn txn: {path.name}") from exc
        try:
            return _validate_project_turn_txn(turn_id, payload)
        except ValueError as exc:
            raise ValueError(f"failed to load project-turn txn: {path.name}") from exc

    def write(self, turn_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = _validate_project_turn_txn(turn_id, payload)
        path = self.path_for(turn_id)
        serialized = json.dumps(normalized, indent=2, ensure_ascii=False)
        try:
            _create_text_atomic(path, serialized)
            return normalized
        except FileExistsError:
            pass
        existing = self.get(turn_id)
        if existing is None:
            raise ValueError(f"project-turn txn disappeared during write: {turn_id}")
        if existing["fingerprint"] != normalized["fingerprint"]:
            raise ValueError(
                f"project-turn txn conflict: different fingerprint already exists for {turn_id}"
            )
        merged = _validate_project_turn_txn(turn_id, _merge_project_turn_txn(existing, normalized))
        if existing == merged:
            return existing
        _write_text_atomic(path, json.dumps(merged, indent=2, ensure_ascii=False))
        return merged

    def delete(self, turn_id: str) -> None:
        self.path_for(turn_id).unlink(missing_ok=True)


class ThreadHistoryStore:
    def __init__(self, store_root: Path, *, read_mode: str = DEFAULT_JSONL_READ_MODE):
        self.history_dir = ensure_dir(store_root / "thread_history")
        self.read_mode = normalize_jsonl_read_mode(read_mode)

    def append(self, record: ThreadRecord) -> None:
        path = self.path_for(record.thread_id)
        latest = self.latest(record.thread_id)
        if latest is not None and latest.to_dict() == record.to_dict():
            return
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def list_thread_history(self, thread_id: str) -> list[ThreadRecord]:
        path = self.path_for(thread_id)
        if path.exists():
            return self._load_history(path, thread_id=thread_id)
        return []

    def path_for(self, thread_id: str) -> Path:
        return self.history_dir / f"{encode_thread_storage_key(thread_id)}.jsonl"

    def _load_history(self, path: Path, *, thread_id: str) -> list[ThreadRecord]:
        records: list[ThreadRecord] = []
        for payload in iter_jsonl(path, read_mode=self.read_mode):
            try:
                records.append(ThreadRecord.from_dict(payload))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"failed to load thread history: {path.name}") from exc

        mismatched = sorted({record.thread_id for record in records if record.thread_id != thread_id})
        if mismatched:
            raise ValueError(f"thread history path mismatch: {path.name} mixes {', '.join(mismatched)} with {thread_id}")

        return records

    def latest(self, thread_id: str) -> ThreadRecord | None:
        history = self.list_thread_history(thread_id)
        return history[-1] if history else None


class ThreadStore:
    def __init__(self, store_root: Path, *, read_mode: str = DEFAULT_JSONL_READ_MODE):
        self.store_root = ensure_dir(store_root)
        self.threads_dir = ensure_dir(self.store_root / "threads")
        self.read_mode = normalize_jsonl_read_mode(read_mode)
        self.history_store = ThreadHistoryStore(self.store_root, read_mode=self.read_mode)

    def get_thread(self, thread_id: str) -> ThreadRecord | None:
        path = self.path_for(thread_id)
        if path.exists():
            return self._load_thread(path, thread_id=thread_id)
        return None

    def _load_thread(self, path: Path, *, thread_id: str) -> ThreadRecord | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("thread snapshot must be a JSON object")
            record = ThreadRecord.from_dict(payload)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"failed to load thread snapshot: {path.name}") from exc

        if record.thread_id != thread_id:
            raise ValueError(f"thread snapshot path mismatch: {path.name} stores {record.thread_id}, not {thread_id}")
        return record

    def upsert_thread(self, record: ThreadRecord) -> ThreadRecord:
        current = self.get_thread(record.thread_id)
        return self.write_thread(record, current=current, append_history=current is not None)

    def repair_thread(
        self,
        record: ThreadRecord,
        *,
        baseline: ThreadRecord | None,
        append_history: bool,
    ) -> ThreadRecord:
        return self.write_thread(record, current=baseline, append_history=append_history)

    def write_snapshot(self, record: ThreadRecord) -> ThreadRecord:
        temp_path = self.write_snapshot_temp(record)
        self.replace_snapshot(record.thread_id, temp_path)
        return record

    def write_snapshot_temp(self, record: ThreadRecord) -> Path:
        path = self.path_for(record.thread_id)
        temp_path = _temp_path_for(path)
        temp_path.write_text(
            json.dumps(record.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return temp_path

    def replace_snapshot(self, thread_id: str, temp_path: Path) -> Path:
        path = self.path_for(thread_id)
        if temp_path.parent != path.parent:
            raise ValueError("snapshot temp file must be in the same directory as the target snapshot")
        if not temp_path.name.startswith(f"{path.name}.") or temp_path.suffix != ".tmp":
            raise ValueError("snapshot temp file name does not match the target snapshot")
        record = self._load_thread(temp_path, thread_id=thread_id)
        try:
            temp_path.replace(path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise
        if record.thread_id != thread_id:
            raise ValueError(f"thread snapshot temp mismatch: {temp_path.name} stores {record.thread_id}")
        return path

    def list_threads(
        self,
        thread_kind: str | None = None,
        status: str | None = None,
        *,
        last_event_at_or_after: datetime | None = None,
        last_event_at_or_before: datetime | None = None,
    ) -> list[ThreadRecord]:
        records: list[ThreadRecord] = []
        paths = sorted(path for path in self.threads_dir.glob("*.json") if is_thread_storage_path(path))
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError("thread snapshot must be a JSON object")
                record = ThreadRecord.from_dict(payload)
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"failed to load thread snapshot: {path.name}") from exc
            expected_name = f"{encode_thread_storage_key(record.thread_id)}.json"
            if path.name != expected_name:
                raise ValueError(f"thread snapshot path mismatch: {path.name} stores {record.thread_id}")
            if thread_kind is not None and record.thread_kind != thread_kind:
                continue
            if status is not None and record.status != status:
                continue
            if last_event_at_or_after is not None or last_event_at_or_before is not None:
                last_event_at = parse_optional_timestamp(record.last_event_at, context="list_threads filter")
                if last_event_at is None:
                    continue
                if last_event_at_or_after is not None and last_event_at < last_event_at_or_after:
                    continue
                if last_event_at_or_before is not None and last_event_at > last_event_at_or_before:
                    continue
            records.append(record)
        return sorted(
            records,
            key=thread_listing_sort_key,
            reverse=True,
        )

    def list_thread_history(self, thread_id: str) -> list[ThreadRecord]:
        return self.history_store.list_thread_history(thread_id)

    def normalize_for_write(self, record: ThreadRecord, *, current: ThreadRecord | None) -> ThreadRecord:
        meta = record.meta
        if current is None:
            if meta.revision < 1:
                meta = ThreadMeta(
                    created_by=meta.created_by,
                    updated_by=meta.updated_by,
                    revision=1,
                    confidence=meta.confidence,
                )
        else:
            meta = ThreadMeta(
                created_by=current.meta.created_by,
                updated_by=record.meta.updated_by or current.meta.updated_by,
                revision=current.meta.revision + 1,
                confidence=record.meta.confidence,
            )
        return ThreadRecord(
            thread_id=record.thread_id,
            thread_kind=record.thread_kind,
            title=record.title,
            status=record.status,
            plan_time=record.plan_time,
            fact_time=record.fact_time,
            content=record.content,
            event_refs=record.event_refs,
            meta=meta,
            first_event_at=record.first_event_at,
            last_event_at=record.last_event_at,
            created_at=current.created_at if current is not None else record.created_at,
            updated_at=record.updated_at,
        )

    def path_for(self, thread_id: str) -> Path:
        return self.threads_dir / f"{encode_thread_storage_key(thread_id)}.json"

    def latest_history(self, thread_id: str) -> ThreadRecord | None:
        return self.history_store.latest(thread_id)

    def append_history(self, record: ThreadRecord) -> ThreadRecord:
        self.history_store.append(record)
        return record

    def write_thread(
        self,
        record: ThreadRecord,
        *,
        current: ThreadRecord | None,
        append_history: bool,
    ) -> ThreadRecord:
        normalized = self.normalize_for_write(record, current=current)
        if append_history and current is not None:
            self.history_store.append(current)
        self.write_snapshot(normalized)
        return normalized


class TimelineStore:
    def __init__(self, store_root: Path, *, read_mode: str = DEFAULT_JSONL_READ_MODE):
        normalized_mode = normalize_jsonl_read_mode(read_mode)
        self.store_root = ensure_dir(store_root)
        self.raw_events = RawEventStore(store_root, read_mode=normalized_mode)
        self.threads = ThreadStore(store_root, read_mode=normalized_mode)
        self.project_turn_txns = ProjectTurnTxnStore(self.store_root)

    def append_raw_event(self, record: RawEventRecord) -> None:
        self.raw_events.append_raw_event(record)

    def append_raw_events_batch(self, records: list[RawEventRecord]) -> None:
        self.raw_events.append_raw_events_batch(records)

    def get_raw_event(self, event_id: str) -> RawEventRecord | None:
        return self.raw_events.get_raw_event(event_id)

    def upsert_thread(self, record: ThreadRecord) -> ThreadRecord:
        return self.threads.upsert_thread(record)

    def repair_thread(
        self,
        record: ThreadRecord,
        *,
        baseline: ThreadRecord | None,
        append_history: bool,
    ) -> ThreadRecord:
        return self.threads.repair_thread(record, baseline=baseline, append_history=append_history)

    def write_thread_snapshot(self, record: ThreadRecord) -> ThreadRecord:
        return self.threads.write_snapshot(record)

    def normalize_thread_for_write(
        self,
        record: ThreadRecord,
        *,
        current: ThreadRecord | None,
    ) -> ThreadRecord:
        return self.threads.normalize_for_write(record, current=current)

    def write_thread_snapshot_temp(self, record: ThreadRecord) -> Path:
        return self.threads.write_snapshot_temp(record)

    def replace_thread_snapshot(self, thread_id: str, temp_path: Path) -> Path:
        return self.threads.replace_snapshot(thread_id, temp_path)

    def get_thread(self, thread_id: str) -> ThreadRecord | None:
        return self.threads.get_thread(thread_id)

    def list_threads(
        self,
        thread_kind: str | None = None,
        status: str | None = None,
        *,
        last_event_at_or_after: datetime | None = None,
        last_event_at_or_before: datetime | None = None,
    ) -> list[ThreadRecord]:
        return self.threads.list_threads(
            thread_kind=thread_kind,
            status=status,
            last_event_at_or_after=last_event_at_or_after,
            last_event_at_or_before=last_event_at_or_before,
        )

    def list_thread_history(self, thread_id: str) -> list[ThreadRecord]:
        return self.threads.list_thread_history(thread_id)

    def latest_thread_history(self, thread_id: str) -> ThreadRecord | None:
        return self.threads.latest_history(thread_id)

    def append_thread_history(self, record: ThreadRecord) -> ThreadRecord:
        return self.threads.append_history(record)

    def get_project_turn_txn(self, turn_id: str) -> dict[str, Any] | None:
        return self.project_turn_txns.get(turn_id)

    def write_project_turn_txn(self, turn_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.project_turn_txns.write(turn_id, payload)

    def delete_project_turn_txn(self, turn_id: str) -> None:
        self.project_turn_txns.delete(turn_id)

    @contextmanager
    def project_turn_write_lock(self, *, turn_id: str, thread_id: str | None) -> Iterator[None]:
        with acquire_project_turn_write_lock(self.store_root, turn_id=turn_id, thread_id=thread_id):
            yield
