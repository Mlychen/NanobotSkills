from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
from scripts.models import RawEventRecord, ThreadContent, ThreadFactTime, ThreadMeta, ThreadPlanTime, ThreadRecord
from scripts.store import TimelineStore
from scripts.timeline_cli import ThreadWritePlan, apply_replay_thread_write_plan


def _raw_event(event_id: str, *, raw_text: str = "hello") -> RawEventRecord:
    return RawEventRecord(
        event_id=event_id,
        event_type="message",
        recorded_at="2026-04-06T10:00:00+00:00",
        source="skill://timeline-memory",
        actor_kind="user",
        actor_id="user",
        raw_text=raw_text,
        payload={"text": raw_text},
    )


def _thread_record(thread_id: str, *, title: str, updated_at: str) -> ThreadRecord:
    return ThreadRecord(
        thread_id=thread_id,
        thread_kind="task",
        title=title,
        status="planned",
        plan_time=ThreadPlanTime(),
        fact_time=ThreadFactTime(),
        content=ThreadContent(notes=title),
        event_refs=[],
        meta=ThreadMeta(created_by="test", updated_by="test", revision=1),
        first_event_at="2026-04-06T10:00:00+00:00",
        last_event_at=updated_at,
        created_at="2026-04-06T10:00:00+00:00",
        updated_at=updated_at,
    )


def test_append_raw_events_batch_writes_all_records(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")

    store.append_raw_events_batch([
        _raw_event("evt-1", raw_text="first"),
        _raw_event("evt-2", raw_text="second"),
    ])

    raw_path = scratch_root / "store" / "raw_events.jsonl"
    lines = [line for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert [json.loads(line)["event_id"] for line in lines] == ["evt-1", "evt-2"]
    assert store.get_raw_event("evt-2") is not None


def test_append_raw_events_batch_rejects_duplicates(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")

    with pytest.raises(ValueError, match="raw event batch contains duplicate event_id: evt-dup"):
        store.append_raw_events_batch([
            _raw_event("evt-dup", raw_text="first"),
            _raw_event("evt-dup", raw_text="second"),
        ])

    store.append_raw_event(_raw_event("evt-existing"))
    store.append_raw_events_batch([_raw_event("evt-existing")])


def test_append_raw_events_batch_retries_partial_commit_by_filling_missing_records(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")
    first = _raw_event("evt-1", raw_text="first")
    second = _raw_event("evt-2", raw_text="second")

    store.append_raw_event(first)
    store.append_raw_events_batch([first, second])

    raw_path = scratch_root / "store" / "raw_events.jsonl"
    lines = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert [line["event_id"] for line in lines] == ["evt-1", "evt-2"]


def test_append_raw_events_batch_rejects_partial_retry_with_conflicting_existing_record(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")
    store.append_raw_event(_raw_event("evt-1", raw_text="first"))

    with pytest.raises(ValueError, match="raw event conflict: different payload already exists: evt-1"):
        store.append_raw_events_batch([
            _raw_event("evt-1", raw_text="changed"),
            _raw_event("evt-2", raw_text="second"),
        ])


def test_project_turn_txn_round_trip_and_delete(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")
    turn_id = "agent:test:0001"
    payload = {
        "turn_id": turn_id,
        "fingerprint": "fp-1",
        "stage": "prepared",
        "required_event_ids": ["evt-1", "evt-2"],
    }

    stored = store.write_project_turn_txn(turn_id, payload)
    txn_path = store.project_turn_txns.path_for(turn_id)

    assert stored == payload
    assert txn_path.exists()
    assert store.get_project_turn_txn(turn_id) == payload

    store.delete_project_turn_txn(turn_id)

    assert store.get_project_turn_txn(turn_id) is None
    assert not txn_path.exists()


def test_project_turn_txn_rejects_inconsistent_payload(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")

    with pytest.raises(ValueError, match="project-turn txn turn_id mismatch"):
        store.write_project_turn_txn(
            "agent:test:0001",
            {
                "turn_id": "agent:test:other",
                "fingerprint": "fp-1",
                "stage": "prepared",
            },
        )

    with pytest.raises(ValueError, match="project-turn txn.stage must be one of"):
        store.write_project_turn_txn(
            "agent:test:0001",
            {
                "turn_id": "agent:test:0001",
                "fingerprint": "fp-1",
                "stage": "unknown",
            },
        )


def test_project_turn_txn_rejects_overwrite_with_different_fingerprint(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")
    turn_id = "agent:test:0001"
    store.write_project_turn_txn(
        turn_id,
        {
            "turn_id": turn_id,
            "fingerprint": "fp-a",
            "stage": "prepared",
        },
    )

    with pytest.raises(ValueError, match="project-turn txn conflict: different fingerprint already exists"):
        store.write_project_turn_txn(
            turn_id,
            {
                "turn_id": turn_id,
                "fingerprint": "fp-b",
                "stage": "prepared",
            },
        )

    assert store.get_project_turn_txn(turn_id)["fingerprint"] == "fp-a"


def test_project_turn_txn_allows_same_fingerprint_update(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")
    turn_id = "agent:test:0001"
    store.write_project_turn_txn(
        turn_id,
        {
            "turn_id": turn_id,
            "fingerprint": "fp-a",
            "stage": "prepared",
            "recorded_at": "2026-04-06T10:00:00+00:00",
            "thread_id": "thr-1",
        },
    )

    updated = store.write_project_turn_txn(
        turn_id,
        {
            "turn_id": turn_id,
            "fingerprint": "fp-a",
            "stage": "raw_committed",
            "required_event_ids": ["evt-1", "evt-2"],
        },
    )

    assert updated["stage"] == "raw_committed"
    assert updated["recorded_at"] == "2026-04-06T10:00:00+00:00"
    assert updated["thread_id"] == "thr-1"
    assert store.get_project_turn_txn(turn_id)["required_event_ids"] == ["evt-1", "evt-2"]


def test_project_turn_txn_rejects_stage_rollback_for_same_fingerprint(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")
    turn_id = "agent:test:0001"
    store.write_project_turn_txn(
        turn_id,
        {
            "turn_id": turn_id,
            "fingerprint": "fp-a",
            "stage": "raw_committed",
        },
    )

    with pytest.raises(ValueError, match="project-turn txn stage rollback is not allowed"):
        store.write_project_turn_txn(
            turn_id,
            {
                "turn_id": turn_id,
                "fingerprint": "fp-a",
                "stage": "prepared",
            },
        )


def test_project_turn_txn_read_rejects_invalid_persisted_payload(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")
    turn_id = "agent:test:0001"
    txn_path = store.project_turn_txns.path_for(turn_id)
    txn_path.write_text(
        json.dumps(
            {
                "turn_id": turn_id,
                "fingerprint": "fp-1",
                "stage": "unknown",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="failed to load project-turn txn"):
        store.get_project_turn_txn(turn_id)


def test_snapshot_temp_write_and_replace(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")
    thread_id = "thr_atomic"
    original = _thread_record(thread_id, title="before", updated_at="2026-04-06T10:00:00+00:00")
    updated = _thread_record(thread_id, title="after", updated_at="2026-04-06T11:00:00+00:00")

    store.write_thread_snapshot(original)
    snapshot_path = store.threads.path_for(thread_id)
    temp_path = store.write_thread_snapshot_temp(updated)

    assert json.loads(snapshot_path.read_text(encoding="utf-8"))["title"] == "before"
    assert temp_path.exists()

    store.replace_thread_snapshot(thread_id, temp_path)

    assert json.loads(snapshot_path.read_text(encoding="utf-8"))["title"] == "after"
    assert not temp_path.exists()


def test_replace_snapshot_rejects_temp_file_for_other_thread(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")
    store.write_thread_snapshot(_thread_record("thr_target", title="target", updated_at="2026-04-06T10:00:00+00:00"))
    wrong_temp_path = store.write_thread_snapshot_temp(
        _thread_record("thr_other", title="other", updated_at="2026-04-06T11:00:00+00:00")
    )

    with pytest.raises(ValueError, match="snapshot temp file name does not match the target snapshot"):
        store.replace_thread_snapshot("thr_target", wrong_temp_path)


def test_replace_snapshot_rejects_temp_payload_with_mismatched_thread_id(scratch_root: Path) -> None:
    store = TimelineStore(scratch_root / "store")
    thread_id = "thr_target"
    snapshot_path = store.threads.path_for(thread_id)
    store.write_thread_snapshot(_thread_record(thread_id, title="before", updated_at="2026-04-06T10:00:00+00:00"))
    malicious_temp_path = snapshot_path.parent / f"{snapshot_path.name}.manual.tmp"
    malicious_temp_path.write_text(
        json.dumps(
            _thread_record("thr_other", title="other", updated_at="2026-04-06T11:00:00+00:00").to_dict(),
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="stores thr_other, not thr_target"):
        store.replace_thread_snapshot(thread_id, malicious_temp_path)


def test_write_thread_appends_history_before_snapshot(scratch_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = TimelineStore(scratch_root / "store")
    current = _thread_record("thr_order", title="before", updated_at="2026-04-06T10:00:00+00:00")
    target = _thread_record("thr_order", title="after", updated_at="2026-04-06T11:00:00+00:00")
    calls: list[str] = []

    def fake_write_snapshot(record: ThreadRecord) -> ThreadRecord:
        calls.append(f"snapshot:{record.title}")
        return record

    def fake_append_history(record: ThreadRecord) -> None:
        calls.append(f"history:{record.title}")

    monkeypatch.setattr(store.threads, "write_snapshot", fake_write_snapshot)
    monkeypatch.setattr(store.threads.history_store, "append", fake_append_history)

    result = store.threads.write_thread(target, current=current, append_history=True)

    assert result.title == "after"
    assert calls == ["history:before", "snapshot:after"]


def test_apply_replay_thread_write_plan_appends_history_before_snapshot(
    scratch_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = TimelineStore(scratch_root / "store")
    target = _thread_record("thr_replay_order", title="after", updated_at="2026-04-06T11:00:00+00:00")
    history = _thread_record("thr_replay_order", title="before", updated_at="2026-04-06T10:00:00+00:00")
    plan = ThreadWritePlan(target_thread=target, history_entry=history)
    calls: list[str] = []

    def fake_write_thread_snapshot(record: ThreadRecord) -> ThreadRecord:
        calls.append(f"snapshot:{record.title}")
        return record

    def fake_append_thread_history(record: ThreadRecord) -> ThreadRecord:
        calls.append(f"history:{record.title}")
        return record

    monkeypatch.setattr(store, "write_thread_snapshot", fake_write_thread_snapshot)
    monkeypatch.setattr(store, "append_thread_history", fake_append_thread_history)

    result = apply_replay_thread_write_plan(store, plan=plan)

    assert result.title == "after"
    assert calls == ["history:before", "snapshot:after"]
