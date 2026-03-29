from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from models import RawEventRecord, ThreadMeta, ThreadRecord


logger = logging.getLogger(__name__)
THREAD_STORAGE_PREFIX = "tid_"


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "item"


def encode_thread_storage_key(thread_id: str) -> str:
    encoded = thread_id.encode("utf-8").hex()
    return f"{THREAD_STORAGE_PREFIX}{encoded}"


def is_canonical_thread_path(path: Path) -> bool:
    return re.fullmatch(rf"{re.escape(THREAD_STORAGE_PREFIX)}[0-9a-f]+", path.stem) is not None


def classify_thread_snapshot_path(path: Path, *, thread_id: str) -> str | None:
    if path.name == f"{encode_thread_storage_key(thread_id)}.json":
        return "canonical"
    if path.name == f"{safe_filename(thread_id)}.json":
        return "legacy"
    return None


def iter_jsonl(path: Path):
    with open(path, encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                logger.warning("Skipping malformed JSONL line %s in %s: %s", line_no, path, exc)
                continue
            if not isinstance(payload, dict):
                logger.warning("Skipping non-object JSONL line %s in %s", line_no, path)
                continue
            yield payload


class RawEventStore:
    def __init__(self, store_root: Path):
        self.store_root = ensure_dir(store_root)
        self.path = self.store_root / "raw_events.jsonl"

    def append_raw_event(self, record: RawEventRecord) -> None:
        if self.get_raw_event(record.event_id) is not None:
            raise ValueError(f"raw event already exists: {record.event_id}")
        ensure_dir(self.path.parent)
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def get_raw_event(self, event_id: str) -> RawEventRecord | None:
        if not self.path.exists():
            return None
        for payload in iter_jsonl(self.path):
            if payload.get("event_id") != event_id:
                continue
            try:
                return RawEventRecord.from_dict(payload)
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Invalid raw event %s in %s: %s", event_id, self.path, exc)
                return None
        return None


class ThreadHistoryStore:
    def __init__(self, store_root: Path):
        self.history_dir = ensure_dir(store_root / "thread_history")

    def append(self, record: ThreadRecord) -> None:
        path = self.path_for(record.thread_id)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")

    def list_thread_history(self, thread_id: str) -> list[ThreadRecord]:
        path = self.path_for(thread_id)
        if path.exists():
            return self._load_history(path, thread_id=thread_id, legacy=False)

        legacy_path = self.legacy_path_for(thread_id)
        if legacy_path.exists():
            return self._load_history(legacy_path, thread_id=thread_id, legacy=True)

        return []

    def path_for(self, thread_id: str) -> Path:
        return self.history_dir / f"{encode_thread_storage_key(thread_id)}.jsonl"

    def legacy_path_for(self, thread_id: str) -> Path:
        return self.history_dir / f"{safe_filename(thread_id)}.jsonl"

    def _load_history(self, path: Path, *, thread_id: str, legacy: bool) -> list[ThreadRecord]:
        records: list[ThreadRecord] = []
        for payload in iter_jsonl(path):
            try:
                records.append(ThreadRecord.from_dict(payload))
            except (KeyError, TypeError, ValueError) as exc:
                logger.warning("Invalid thread history entry for %s in %s: %s", thread_id, path, exc)

        mismatched = sorted({record.thread_id for record in records if record.thread_id != thread_id})
        if mismatched:
            path_kind = "legacy" if legacy else "canonical"
            raise ValueError(
                f"{path_kind} thread history path collision: {path.name} mixes {', '.join(mismatched)} with {thread_id}"
            )

        return records


class ThreadStore:
    def __init__(self, store_root: Path):
        self.store_root = ensure_dir(store_root)
        self.threads_dir = ensure_dir(self.store_root / "threads")
        self.history_store = ThreadHistoryStore(self.store_root)

    def get_thread(self, thread_id: str) -> ThreadRecord | None:
        path = self.path_for(thread_id)
        if path.exists():
            return self._load_thread(path, thread_id=thread_id, legacy=False)

        legacy_path = self.legacy_path_for(thread_id)
        if legacy_path.exists():
            return self._load_thread(legacy_path, thread_id=thread_id, legacy=True)

        return None

    def _load_thread(self, path: Path, *, thread_id: str, legacy: bool) -> ThreadRecord | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            record = ThreadRecord.from_dict(payload)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            logger.warning("Failed to load thread %s from %s: %s", thread_id, path, exc)
            return None

        if record.thread_id != thread_id:
            path_kind = "legacy" if legacy else "canonical"
            raise ValueError(f"{path_kind} thread path collision: {path.name} stores {record.thread_id}, not {thread_id}")
        return record

    def upsert_thread(self, record: ThreadRecord) -> ThreadRecord:
        current = self.get_thread(record.thread_id)
        normalized = self.normalize_for_write(record, current=current)
        if current is not None:
            self.history_store.append(current)
        self.path_for(normalized.thread_id).write_text(
            json.dumps(normalized.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return normalized

    def list_threads(self, thread_kind: str | None = None, status: str | None = None) -> list[ThreadRecord]:
        records_by_id: dict[str, tuple[int, ThreadRecord]] = {}
        paths = sorted(self.threads_dir.glob("*.json"), key=lambda path: (not is_canonical_thread_path(path), path.name))
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                record = ThreadRecord.from_dict(payload)
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                logger.warning("Skipping invalid thread snapshot %s: %s", path, exc)
                continue
            path_kind = classify_thread_snapshot_path(path, thread_id=record.thread_id)
            if path_kind is None:
                logger.warning("Skipping unsupported thread snapshot %s for thread_id %s", path, record.thread_id)
                continue
            if thread_kind and record.thread_kind != thread_kind:
                continue
            if status and record.status != status:
                continue
            priority = 0 if path_kind == "canonical" else 1
            existing = records_by_id.get(record.thread_id)
            if existing is None or priority < existing[0]:
                records_by_id[record.thread_id] = (priority, record)
        return sorted(
            (item[1] for item in records_by_id.values()),
            key=lambda item: (item.last_event_at or "", item.updated_at),
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

    def legacy_path_for(self, thread_id: str) -> Path:
        return self.threads_dir / f"{safe_filename(thread_id)}.json"


class TimelineStore:
    def __init__(self, store_root: Path):
        self.raw_events = RawEventStore(store_root)
        self.threads = ThreadStore(store_root)

    def append_raw_event(self, record: RawEventRecord) -> None:
        self.raw_events.append_raw_event(record)

    def get_raw_event(self, event_id: str) -> RawEventRecord | None:
        return self.raw_events.get_raw_event(event_id)

    def upsert_thread(self, record: ThreadRecord) -> ThreadRecord:
        return self.threads.upsert_thread(record)

    def get_thread(self, thread_id: str) -> ThreadRecord | None:
        return self.threads.get_thread(thread_id)

    def list_threads(self, thread_kind: str | None = None, status: str | None = None) -> list[ThreadRecord]:
        return self.threads.list_threads(thread_kind=thread_kind, status=status)

    def list_thread_history(self, thread_id: str) -> list[ThreadRecord]:
        return self.threads.list_thread_history(thread_id)
