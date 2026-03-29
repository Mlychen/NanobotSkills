from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from base64 import urlsafe_b64encode
from pathlib import Path

from models import ProjectTurnInput, RawEventRecord
from store import encode_thread_storage_key, safe_filename


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "timeline_cli.py"
DEFAULT_SOURCE = "skill://timeline-memory"
TIMELINE_META_KEY = "_timeline_memory"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def run_process(store_root: Path, *args: str, payload: dict | None = None) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, str(CLI), *args, "--store-root", str(store_root)]
    if payload is not None:
        input_path = store_root.parent / "input.json"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        command.extend(["--input", str(input_path)])
    return subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_env(),
        check=False,
    )


def run_cli(store_root: Path, payload: dict, *args: str) -> dict | list | None:
    result = run_process(store_root, *args, payload=payload)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "timeline selftest command failed")
    return json.loads(result.stdout)


def run_read(store_root: Path, *args: str) -> dict | list | None:
    result = run_process(store_root, *args)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "timeline selftest read failed")
    return json.loads(result.stdout)


def expect_failure(store_root: Path, *args: str, payload: dict | None = None) -> str:
    result = run_process(store_root, *args, payload=payload)
    if result.returncode == 0:
        raise RuntimeError("expected CLI failure")
    return result.stderr.strip()


def append_raw_event(store_root: Path, record: RawEventRecord) -> None:
    raw_path = store_root / "raw_events.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record.to_dict(), ensure_ascii=False) + "\n")


def build_raw_event(payload: dict, *, role: str) -> RawEventRecord:
    turn_input = ProjectTurnInput.from_dict(payload)
    fingerprint = turn_input.fingerprint()
    recorded_at = turn_input.context.recorded_at or "2026-03-24T00:00:00+08:00"
    source = turn_input.context.source or DEFAULT_SOURCE
    thread_id = turn_input.thread.thread_id if turn_input.thread is not None else None
    actor_kind = "user" if role == "inbound" else "assistant"
    actor_id = turn_input.context.actor_id or "user"
    raw_text = turn_input.user_text
    event_type = "user_message"
    event_id = f"{turn_input.turn_id}:in"
    message = turn_input.user_text

    if role == "outbound":
        if turn_input.assistant_text is None:
            raise RuntimeError("assistant_text is required for outbound raw event")
        actor_id = turn_input.context.assistant_actor_id or "assistant"
        raw_text = turn_input.assistant_text
        event_type = "assistant_response"
        event_id = f"{turn_input.turn_id}:out"
        message = turn_input.assistant_text

    return RawEventRecord(
        event_id=event_id,
        event_type=event_type,
        recorded_at=recorded_at,
        source=source,
        actor_kind=actor_kind,
        actor_id=actor_id,
        raw_text=raw_text,
        payload={
            "message": message,
            TIMELINE_META_KEY: {
                "turn_id": turn_input.turn_id,
                "role": role,
                "fingerprint": fingerprint,
                "thread_id": thread_id,
            },
        },
        schema_version=1,
        created_at=recorded_at,
    )


def assert_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise RuntimeError(f"{message}: expected {expected!r}, got {actual!r}")


def assert_in(needle: str, haystack: str, message: str) -> None:
    if needle not in haystack:
        raise RuntimeError(f"{message}: missing {needle!r} in {haystack!r}")


def test_regression_basics(temp_root: Path) -> None:
    store_root = temp_root / "timeline-store"
    create_payload = {
        "turn_id": "agent:selftest:0001",
        "user_text": "下周三下午三点交电费。",
        "assistant_text": "记下了，下周三下午三点交电费。",
        "thread": {
            "thread_id": "thr_selftest_pay_bill",
            "title": "交电费",
            "status": "planned",
            "content": {"notes": "从手机银行支付", "items": ["手机银行"]},
            "plan_time": {"due_at": "2026-03-25T15:00:00+08:00"},
        },
        "context": {
            "source": "selftest",
            "recorded_at": "2026-03-24T10:00:00+08:00",
            "actor_id": "user_001",
            "assistant_actor_id": "nanobot",
        },
    }
    create = run_cli(store_root, create_payload, "project-turn")
    assert_equal(
        create["recorded_event_ids"],
        ["agent:selftest:0001:in", "agent:selftest:0001:out"],
        "unexpected event ids from initial project-turn",
    )

    replay = run_cli(store_root, create_payload, "project-turn")
    assert_equal(replay["idempotent_replay"], True, "replay did not return idempotent_replay=true")

    update = run_cli(
        store_root,
        {
            "turn_id": "agent:selftest:0002",
            "user_text": "改成周三下午四点交电费。",
            "assistant_text": "已更新到下午四点。",
            "thread": {
                "thread_id": "thr_selftest_pay_bill",
                "title": "交电费",
                "status": "planned",
                "content": {"notes": "从手机银行支付"},
                "plan_time": {"due_at": "2026-03-25T16:00:00+08:00"},
            },
            "context": {
                "source": "selftest",
                "recorded_at": "2026-03-24T11:00:00+08:00",
                "actor_id": "user_001",
                "assistant_actor_id": "nanobot",
            },
        },
        "project-turn",
    )
    assert_equal(update["thread"]["meta"]["revision"], 2, "thread revision did not increment to 2")

    thread = run_read(store_root, "get-thread", "--thread-id", "thr_selftest_pay_bill")
    history = run_read(store_root, "list-thread-history", "--thread-id", "thr_selftest_pay_bill")
    threads = run_read(store_root, "list-threads", "--status", "planned")
    assert_equal(thread["plan_time"]["due_at"], "2026-03-25T16:00:00+08:00", "thread query returned unexpected due_at")
    assert_equal(len(history), 1, "thread history length mismatch")
    assert_equal(len(threads), 1, "list-threads returned unexpected count")
    assert_equal(
        len((store_root / "raw_events.jsonl").read_text(encoding="utf-8").splitlines()),
        4,
        "raw event count mismatch",
    )


def test_thread_id_path_isolation(temp_root: Path) -> None:
    store_root = temp_root / "collision-store"
    run_cli(
        store_root,
        {
            "turn_id": "agent:collision:0001",
            "user_text": "记录 slash 线程。",
            "thread": {"thread_id": "thr/a", "title": "slash", "status": "planned"},
        },
        "project-turn",
    )
    run_cli(
        store_root,
        {
            "turn_id": "agent:collision:0002",
            "user_text": "记录 underscore 线程。",
            "thread": {"thread_id": "thr_a", "title": "underscore", "status": "done"},
        },
        "project-turn",
    )
    run_cli(
        store_root,
        {
            "turn_id": "agent:collision:0003",
            "user_text": "更新 slash 线程。",
            "thread": {"thread_id": "thr/a", "title": "slash-updated", "status": "planned"},
        },
        "project-turn",
    )

    slash = run_read(store_root, "get-thread", "--thread-id", "thr/a")
    underscore = run_read(store_root, "get-thread", "--thread-id", "thr_a")
    slash_history = run_read(store_root, "list-thread-history", "--thread-id", "thr/a")
    underscore_history = run_read(store_root, "list-thread-history", "--thread-id", "thr_a")
    threads = run_read(store_root, "list-threads")

    assert_equal(slash["title"], "slash-updated", "slash thread title mismatch")
    assert_equal(underscore["title"], "underscore", "underscore thread title mismatch")
    assert_equal(len(slash_history), 1, "slash thread history length mismatch")
    assert_equal(len(underscore_history), 0, "underscore thread history should be empty")
    assert_equal(len(threads), 2, "list-threads should return two independent threads")

    thread_files = sorted(path.name for path in (store_root / "threads").glob("*.json"))
    history_files = sorted(path.name for path in (store_root / "thread_history").glob("*.jsonl"))
    assert_equal(len(thread_files), 2, "canonical thread snapshots should use two distinct files")
    assert_equal(len(history_files), 1, "only slash thread should have a history file")


def test_canonical_path_case_insensitive_safety(temp_root: Path) -> None:
    store_root = temp_root / "case-safe-store"
    first_thread_id = "aaa"
    second_thread_id = "aaG"
    run_cli(
        store_root,
        {
            "turn_id": "agent:case-safe:0001",
            "user_text": "first canonical thread",
            "thread": {"thread_id": first_thread_id, "title": "first", "status": "planned"},
        },
        "project-turn",
    )
    run_cli(
        store_root,
        {
            "turn_id": "agent:case-safe:0002",
            "user_text": "second canonical thread",
            "thread": {"thread_id": second_thread_id, "title": "second", "status": "planned"},
        },
        "project-turn",
    )

    first = run_read(store_root, "get-thread", "--thread-id", first_thread_id)
    second = run_read(store_root, "get-thread", "--thread-id", second_thread_id)
    first_history = run_read(store_root, "list-thread-history", "--thread-id", first_thread_id)
    second_history = run_read(store_root, "list-thread-history", "--thread-id", second_thread_id)
    thread_files = sorted(path.name for path in (store_root / "threads").glob("*.json"))

    assert_equal(first["title"], "first", "first case-safe thread title mismatch")
    assert_equal(second["title"], "second", "second case-safe thread title mismatch")
    assert_equal(len(first_history), 0, "first case-safe thread history should be empty")
    assert_equal(len(second_history), 0, "second case-safe thread history should be empty")
    assert_equal(len(thread_files), 2, "case-safe canonical encoding should create two files")
    assert_equal(
        thread_files,
        sorted([
            f"{encode_thread_storage_key(first_thread_id)}.json",
            f"{encode_thread_storage_key(second_thread_id)}.json",
        ]),
        "canonical file names should use lowercase hex encoding",
    )


def test_legacy_compatibility_and_collision_detection(temp_root: Path) -> None:
    store_root = temp_root / "legacy-store"
    create_payload = {
        "turn_id": "agent:legacy:0001",
        "user_text": "创建 legacy 线程。",
        "thread": {"thread_id": "thr/legacy", "title": "legacy", "status": "planned"},
        "context": {"recorded_at": "2026-03-24T09:00:00+08:00"},
    }
    update_payload = {
        "turn_id": "agent:legacy:0002",
        "user_text": "更新 legacy 线程。",
        "thread": {"thread_id": "thr/legacy", "title": "legacy-2", "status": "done"},
        "context": {"recorded_at": "2026-03-24T10:00:00+08:00"},
    }
    run_cli(store_root, create_payload, "project-turn")
    run_cli(store_root, update_payload, "project-turn")

    thread = run_read(store_root, "get-thread", "--thread-id", "thr/legacy")
    history = run_read(store_root, "list-thread-history", "--thread-id", "thr/legacy")
    canonical_thread = store_root / "threads" / f"{encode_thread_storage_key('thr/legacy')}.json"
    canonical_history = store_root / "thread_history" / f"{encode_thread_storage_key('thr/legacy')}.jsonl"
    legacy_thread = store_root / "threads" / "thr_legacy.json"
    legacy_history = store_root / "thread_history" / "thr_legacy.jsonl"

    legacy_thread.write_text(json.dumps(thread, ensure_ascii=False, indent=2), encoding="utf-8")
    legacy_history.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in history) + "\n",
        encoding="utf-8",
    )
    canonical_thread.unlink()
    canonical_history.unlink()

    legacy_thread_result = run_read(store_root, "get-thread", "--thread-id", "thr/legacy")
    legacy_history_result = run_read(store_root, "list-thread-history", "--thread-id", "thr/legacy")
    assert_equal(legacy_thread_result["title"], "legacy-2", "legacy snapshot fallback failed")
    assert_equal(len(legacy_history_result), 1, "legacy history fallback failed")

    mismatch_store = temp_root / "legacy-mismatch-store"
    (mismatch_store / "threads").mkdir(parents=True, exist_ok=True)
    (mismatch_store / "thread_history").mkdir(parents=True, exist_ok=True)
    mismatch_thread = dict(thread)
    mismatch_thread["thread_id"] = "thr_x"
    (mismatch_store / "threads" / "thr_x.json").write_text(
        json.dumps(mismatch_thread, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (mismatch_store / "thread_history" / "thr_x.jsonl").write_text(
        json.dumps(mismatch_thread, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    thread_error = expect_failure(mismatch_store, "get-thread", "--thread-id", "thr/x")
    history_error = expect_failure(mismatch_store, "list-thread-history", "--thread-id", "thr/x")
    assert_in("legacy thread path collision", thread_error, "legacy thread collision should fail explicitly")
    assert_in("legacy thread history path collision", history_error, "legacy history collision should fail explicitly")


def test_implicit_thread_id_is_stable_and_collision_free(temp_root: Path) -> None:
    store_root = temp_root / "derived-thread-id-store"
    first_payload = {
        "turn_id": "agent:a/b:1",
        "user_text": "first implicit thread",
        "thread": {"title": "first-derived", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:a_b:1",
        "user_text": "second implicit thread",
        "thread": {"title": "second-derived", "status": "done"},
    }
    first = run_cli(store_root, first_payload, "project-turn")
    second = run_cli(store_root, second_payload, "project-turn")
    threads = run_read(store_root, "list-threads")

    expected_first_thread_id = f"thr_{first_payload['turn_id'].encode('utf-8').hex()}"
    expected_second_thread_id = f"thr_{second_payload['turn_id'].encode('utf-8').hex()}"
    assert_equal(first["thread"]["thread_id"], expected_first_thread_id, "first derived thread_id mismatch")
    assert_equal(second["thread"]["thread_id"], expected_second_thread_id, "second derived thread_id mismatch")
    assert_equal(len(threads), 2, "derived thread IDs should not collide")
    assert_equal(
        sorted(thread["thread_id"] for thread in threads),
        sorted([expected_first_thread_id, expected_second_thread_id]),
        "list-threads should preserve two distinct derived thread_ids",
    )


def test_list_threads_skips_unsupported_legacy_canonical_paths(temp_root: Path) -> None:
    thread_id = "thr/legacy-base64"
    legacy_store = temp_root / "unsupported-legacy-canonical-store"
    thread_payload = run_cli(
        temp_root / "unsupported-template-store",
        {
            "turn_id": "agent:legacy-list:0001",
            "user_text": "template thread",
            "thread": {"thread_id": thread_id, "title": "current-title", "status": "planned"},
            "context": {"recorded_at": "2026-03-24T15:00:00+08:00"},
        },
        "project-turn",
    )["thread"]

    legacy_name = urlsafe_b64encode(thread_id.encode("utf-8")).decode("ascii").rstrip("=")
    legacy_path = legacy_store / "threads" / f"tid_{legacy_name}.json"
    legacy_path.parent.mkdir(parents=True, exist_ok=True)
    legacy_path.write_text(json.dumps(thread_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    assert_equal(run_read(legacy_store, "list-threads"), [], "unsupported legacy canonical snapshot should be hidden")
    assert_equal(run_read(legacy_store, "get-thread", "--thread-id", thread_id), None, "get-thread should not load unsupported legacy canonical snapshot")

    mixed_store = temp_root / "mixed-canonical-store"
    current_result = run_cli(
        mixed_store,
        {
            "turn_id": "agent:legacy-list:0002",
            "user_text": "current thread",
            "thread": {"thread_id": thread_id, "title": "current-title", "status": "planned"},
            "context": {"recorded_at": "2026-03-24T16:00:00+08:00"},
        },
        "project-turn",
    )
    stale_thread = dict(current_result["thread"])
    stale_thread["title"] = "stale-legacy-title"
    (mixed_store / "threads" / f"tid_{legacy_name}.json").write_text(
        json.dumps(stale_thread, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    mixed_threads = run_read(mixed_store, "list-threads")
    assert_equal(len(mixed_threads), 1, "unsupported legacy canonical snapshot should not create duplicates")
    assert_equal(mixed_threads[0]["title"], "current-title", "canonical snapshot should win over unsupported legacy canonical snapshot")


def test_replay_accepts_legacy_implicit_thread_ids(temp_root: Path) -> None:
    full_store = temp_root / "legacy-derived-replay-store"
    payload = {
        "turn_id": "agent:a/b:legacy",
        "user_text": "legacy implicit inbound",
        "assistant_text": "legacy implicit outbound",
        "thread": {"title": "legacy-derived", "status": "planned"},
        "context": {"recorded_at": "2026-03-24T17:00:00+08:00"},
    }
    legacy_thread_id = f"thr_{safe_filename(payload['turn_id'])}"
    canonical_legacy_path = full_store / "threads" / f"{encode_thread_storage_key(legacy_thread_id)}.json"
    canonical_legacy_path.parent.mkdir(parents=True, exist_ok=True)

    inbound = build_raw_event(payload, role="inbound")
    inbound.payload[TIMELINE_META_KEY]["thread_id"] = legacy_thread_id
    outbound = build_raw_event(payload, role="outbound")
    outbound.payload[TIMELINE_META_KEY]["thread_id"] = legacy_thread_id
    append_raw_event(full_store, inbound)
    append_raw_event(full_store, outbound)

    recorded_at = payload["context"]["recorded_at"]
    canonical_legacy_path.write_text(
        json.dumps(
            {
                "thread_id": legacy_thread_id,
                "thread_kind": "task",
                "title": "legacy-derived",
                "status": "planned",
                "plan_time": {},
                "fact_time": {},
                "content": {},
                "event_refs": [
                    {
                        "event_id": inbound.event_id,
                        "role": "primary",
                        "added_at": recorded_at,
                        "added_by": DEFAULT_SOURCE,
                    },
                    {
                        "event_id": outbound.event_id,
                        "role": "context",
                        "added_at": recorded_at,
                        "added_by": DEFAULT_SOURCE,
                    },
                ],
                "meta": {
                    "created_by": DEFAULT_SOURCE,
                    "updated_by": DEFAULT_SOURCE,
                    "revision": 1,
                    "confidence": None,
                },
                "first_event_at": recorded_at,
                "last_event_at": recorded_at,
                "created_at": recorded_at,
                "updated_at": recorded_at,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    replay = run_cli(full_store, payload, "project-turn")
    assert_equal(replay["idempotent_replay"], True, "legacy implicit thread_id should remain idempotent on replay")
    assert_equal(replay["thread"]["thread_id"], legacy_thread_id, "replay should return the stored legacy implicit thread_id")

    repair_store = temp_root / "legacy-derived-repair-store"
    repair_inbound = build_raw_event(payload, role="inbound")
    repair_inbound.payload[TIMELINE_META_KEY]["thread_id"] = legacy_thread_id
    append_raw_event(repair_store, repair_inbound)

    repaired = run_cli(repair_store, payload, "project-turn")
    assert_equal(repaired["idempotent_replay"], False, "legacy implicit partial write should repair instead of conflicting")
    assert_equal(repaired["thread"]["thread_id"], legacy_thread_id, "repair should preserve legacy implicit thread_id")
    assert_equal(run_read(repair_store, "get-thread", "--thread-id", legacy_thread_id)["title"], "legacy-derived", "repair should write the thread snapshot under the stored legacy implicit thread_id")
    assert_equal(
        run_read(repair_store, "get-thread", "--thread-id", f"thr_{payload['turn_id'].encode('utf-8').hex()}"),
        None,
        "repair should not rewrite legacy implicit thread_id to the new derived form",
    )


def test_source_normalization_and_partial_write_recovery(temp_root: Path) -> None:
    source_store = temp_root / "source-store"
    source_result = run_cli(
        source_store,
        {
            "turn_id": "agent:source:0001",
            "user_text": "source 为空字符串。",
            "thread": {"thread_id": "thr_source", "title": "source", "status": "planned"},
            "context": {"source": "", "recorded_at": "2026-03-24T12:00:00+08:00"},
        },
        "project-turn",
    )
    raw_event = json.loads((source_store / "raw_events.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert_equal(raw_event["source"], DEFAULT_SOURCE, "raw event source should normalize to default")
    assert_equal(
        source_result["thread"]["event_refs"][0]["added_by"],
        DEFAULT_SOURCE,
        "thread event ref source should normalize to default",
    )
    assert_equal(
        source_result["thread"]["meta"]["created_by"],
        DEFAULT_SOURCE,
        "thread meta source should normalize to default",
    )

    inbound_only_store = temp_root / "repair-inbound-store"
    inbound_only_payload = {
        "turn_id": "agent:repair:0001",
        "user_text": "先写入 inbound。",
        "assistant_text": "再补 outbound。",
        "thread": {"thread_id": "thr_repair_inbound", "title": "repair", "status": "planned"},
        "context": {"recorded_at": "2026-03-24T13:00:00+08:00"},
    }
    append_raw_event(inbound_only_store, build_raw_event(inbound_only_payload, role="inbound"))
    inbound_recovery = run_cli(inbound_only_store, inbound_only_payload, "project-turn")
    assert_equal(inbound_recovery["idempotent_replay"], False, "repair path should not report idempotent replay")
    assert_equal(
        inbound_recovery["recorded_event_ids"],
        ["agent:repair:0001:in", "agent:repair:0001:out"],
        "inbound-only repair should append outbound event",
    )
    assert_equal(
        len((inbound_only_store / "raw_events.jsonl").read_text(encoding="utf-8").splitlines()),
        2,
        "inbound-only repair should leave exactly two raw events",
    )
    assert_equal(inbound_recovery["thread"]["meta"]["revision"], 1, "repaired thread should start at revision 1")

    missing_thread_store = temp_root / "repair-thread-store"
    missing_thread_payload = {
        "turn_id": "agent:repair:0002",
        "user_text": "raw events 已存在。",
        "assistant_text": "补写线程。",
        "thread": {"thread_id": "thr_repair_thread", "title": "repair-thread", "status": "planned"},
        "context": {"recorded_at": "2026-03-24T14:00:00+08:00"},
    }
    append_raw_event(missing_thread_store, build_raw_event(missing_thread_payload, role="inbound"))
    append_raw_event(missing_thread_store, build_raw_event(missing_thread_payload, role="outbound"))
    missing_thread_recovery = run_cli(missing_thread_store, missing_thread_payload, "project-turn")
    assert_equal(
        missing_thread_recovery["recorded_event_ids"],
        ["agent:repair:0002:in", "agent:repair:0002:out"],
        "missing-thread repair should reuse existing raw event ids",
    )
    assert_equal(
        missing_thread_recovery["thread"]["title"],
        "repair-thread",
        "missing-thread repair should rebuild the thread snapshot",
    )
    assert_equal(
        len((missing_thread_store / "raw_events.jsonl").read_text(encoding="utf-8").splitlines()),
        2,
        "missing-thread repair should not duplicate raw events",
    )

    conflict_store = temp_root / "repair-conflict-store"
    conflict_existing = {
        "turn_id": "agent:repair:0003",
        "user_text": "旧内容",
        "thread": {"thread_id": "thr_repair_conflict", "title": "conflict", "status": "planned"},
    }
    conflict_retry = {
        "turn_id": "agent:repair:0003",
        "user_text": "新内容",
        "thread": {"thread_id": "thr_repair_conflict", "title": "conflict", "status": "planned"},
    }
    append_raw_event(conflict_store, build_raw_event(conflict_existing, role="inbound"))
    conflict_error = expect_failure(conflict_store, "project-turn", payload=conflict_retry)
    assert_in("different payload already recorded", conflict_error, "conflicting retry should still fail")


def main() -> int:
    temp_root = ROOT / "tmp" / "selftest-run"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        test_regression_basics(temp_root)
        test_thread_id_path_isolation(temp_root)
        test_canonical_path_case_insensitive_safety(temp_root)
        test_legacy_compatibility_and_collision_detection(temp_root)
        test_implicit_thread_id_is_stable_and_collision_free(temp_root)
        test_list_threads_skips_unsupported_legacy_canonical_paths(temp_root)
        test_replay_accepts_legacy_implicit_thread_ids(temp_root)
        test_source_normalization_and_partial_write_recovery(temp_root)
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root)

    print("timeline-memory selftest passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
