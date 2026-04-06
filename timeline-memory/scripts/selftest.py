from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from models import ProjectTurnInput, RawEventRecord
from store import encode_thread_storage_key


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "timeline_cli.py"
DEFAULT_SOURCE = "skill://timeline-memory"
TIMELINE_META_KEY = "_timeline_memory"


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _resolve_python_command() -> list[str]:
    if sys.executable and Path(sys.executable).exists():
        return [sys.executable]
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("current python executable is unavailable and uv not found on PATH")
    return [uv, "run", "python"]


def run_process(store_root: Path, *args: str, payload: dict | None = None) -> subprocess.CompletedProcess[str]:
    command = [*_resolve_python_command(), str(CLI), *args, "--store-root", str(store_root)]
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


def append_raw_event(store_root: Path, record: RawEventRecord | dict[str, object]) -> None:
    raw_path = store_root / "raw_events.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    payload = record.to_dict() if isinstance(record, RawEventRecord) else dict(record)
    with open(raw_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def build_raw_event(payload: dict, *, role: str, recorded_at: str = "2026-03-24T00:00:00+08:00") -> RawEventRecord:
    turn_input = ProjectTurnInput.from_dict(payload)
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
                "fingerprint": turn_input.fingerprint(),
                "thread_id": thread_id,
            },
        },
        schema_version=1,
    )


def raw_event_lines(store_root: Path) -> list[str]:
    raw_path = store_root / "raw_events.jsonl"
    if not raw_path.exists():
        return []
    return [line for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def thread_history_path(store_root: Path, thread_id: str) -> Path:
    return store_root / "thread_history" / f"{encode_thread_storage_key(thread_id)}.jsonl"


def thread_snapshot_path(store_root: Path, thread_id: str) -> Path:
    return store_root / "threads" / f"{encode_thread_storage_key(thread_id)}.json"


def project_turn_txn_path(store_root: Path, turn_id: str) -> Path:
    return store_root / "_txn" / "project_turn" / f"turn_{turn_id.encode('utf-8').hex()}.json"


def write_project_turn_txn(store_root: Path, turn_id: str, payload: dict[str, object]) -> Path:
    path = project_turn_txn_path(store_root, turn_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def raw_event_records_for_turn(store_root: Path, turn_id: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for line in raw_event_lines(store_root):
        record = json.loads(line)
        if str(record["event_id"]).startswith(f"{turn_id}:"):
            records.append(record)
    return records


def event_ref_ids(thread_payload: dict[str, object]) -> list[str]:
    refs = thread_payload.get("event_refs", [])
    if not isinstance(refs, list):
        raise RuntimeError("thread payload event_refs must be a list")
    return [
        str(ref["event_id"])
        for ref in refs
        if isinstance(ref, dict) and ref.get("event_id") is not None
    ]


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
    assert_equal(len(raw_event_lines(store_root)), 4, "raw event count mismatch")


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


def test_thread_path_case_insensitive_safety(temp_root: Path) -> None:
    store_root = temp_root / "case-safe-store"
    first_thread_id = "aaa"
    second_thread_id = "aaG"
    run_cli(
        store_root,
        {
            "turn_id": "agent:case-safe:0001",
            "user_text": "first encoded thread",
            "thread": {"thread_id": first_thread_id, "title": "first", "status": "planned"},
        },
        "project-turn",
    )
    run_cli(
        store_root,
        {
            "turn_id": "agent:case-safe:0002",
            "user_text": "second encoded thread",
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
    assert_equal(
        thread_files,
        sorted([
            f"{encode_thread_storage_key(first_thread_id)}.json",
            f"{encode_thread_storage_key(second_thread_id)}.json",
        ]),
        "encoded file names should use lowercase hex encoding",
    )


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
    assert_equal(
        sorted(thread["thread_id"] for thread in threads),
        sorted([expected_first_thread_id, expected_second_thread_id]),
        "list-threads should preserve two distinct derived thread_ids",
    )


def test_source_normalization_and_partial_write_recovery(temp_root: Path) -> None:
    source_store = temp_root / "source-store"
    source_result = run_cli(
        source_store,
        {
            "turn_id": "agent:source:0001",
            "user_text": "source 为空字符串。",
            "thread": {"thread_id": "thr_source", "title": "source", "status": "planned"},
            "context": {"source": ""},
        },
        "project-turn",
    )
    raw_event = json.loads(raw_event_lines(source_store)[0])
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
    }
    template_store = temp_root / "repair-template-store"
    run_cli(template_store, inbound_only_payload, "project-turn")
    append_raw_event(inbound_only_store, json.loads(raw_event_lines(template_store)[0]))
    inbound_recovery = run_cli(inbound_only_store, inbound_only_payload, "project-turn")
    assert_equal(inbound_recovery["idempotent_replay"], False, "repair path should not report idempotent replay")
    assert_equal(
        inbound_recovery["recorded_event_ids"],
        ["agent:repair:0001:in", "agent:repair:0001:out"],
        "inbound-only repair should append outbound event",
    )
    assert_equal(len(raw_event_lines(inbound_only_store)), 2, "inbound-only repair should leave exactly two raw events")
    assert_equal(inbound_recovery["thread"]["meta"]["revision"], 1, "repaired thread should start at revision 1")

    no_thread_store = temp_root / "repair-no-thread-store"
    no_thread_payload = {
        "turn_id": "agent:repair:no-thread:0001",
        "user_text": "只有 inbound raw event。",
        "assistant_text": "补齐 outbound。",
    }
    no_thread_template_store = temp_root / "repair-no-thread-template-store"
    run_cli(no_thread_template_store, no_thread_payload, "project-turn")
    append_raw_event(no_thread_store, json.loads(raw_event_lines(no_thread_template_store)[0]))
    no_thread_recovery = run_cli(no_thread_store, no_thread_payload, "project-turn")
    assert_equal(no_thread_recovery["idempotent_replay"], False, "no-thread repair should not report idempotent replay")
    assert_equal(
        no_thread_recovery["recorded_event_ids"],
        ["agent:repair:no-thread:0001:in", "agent:repair:no-thread:0001:out"],
        "no-thread repair should append outbound event",
    )
    assert_equal(no_thread_recovery["thread"], None, "no-thread repair should keep thread payload empty")
    assert_equal(len(raw_event_lines(no_thread_store)), 2, "no-thread repair should leave exactly two raw events")

    missing_thread_store = temp_root / "repair-thread-store"
    missing_thread_payload = {
        "turn_id": "agent:repair:0002",
        "user_text": "raw events 已存在。",
        "assistant_text": "补写线程。",
        "thread": {"thread_id": "thr_repair_thread", "title": "repair-thread", "status": "planned"},
    }
    run_cli(missing_thread_store, missing_thread_payload, "project-turn")
    for path in (missing_thread_store / "threads").glob("*.json"):
        path.unlink()
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
    assert_equal(len(raw_event_lines(missing_thread_store)), 2, "missing-thread repair should not duplicate raw events")

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


def test_list_threads_orders_by_absolute_time(temp_root: Path) -> None:
    store_root = temp_root / "mixed-offset-order-store"
    threads_dir = store_root / "threads"
    threads_dir.mkdir(parents=True, exist_ok=True)
    (threads_dir / f"{encode_thread_storage_key('thr_late_utc')}.json").write_text(
        json.dumps(
            {
                "thread_id": "thr_late_utc",
                "thread_kind": "task",
                "title": "late-utc",
                "status": "planned",
                "plan_time": {},
                "fact_time": {},
                "content": {},
                "event_refs": [],
                "meta": {"created_by": "selftest", "updated_by": "selftest", "revision": 1},
                "first_event_at": "2026-03-24T09:00:00+00:00",
                "last_event_at": "2026-03-24T09:00:00+00:00",
                "created_at": "2026-03-24T09:00:00+00:00",
                "updated_at": "2026-03-24T09:00:00+00:00",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (threads_dir / f"{encode_thread_storage_key('thr_early_hk')}.json").write_text(
        json.dumps(
            {
                "thread_id": "thr_early_hk",
                "thread_kind": "task",
                "title": "early-hk",
                "status": "planned",
                "plan_time": {},
                "fact_time": {},
                "content": {},
                "event_refs": [],
                "meta": {"created_by": "selftest", "updated_by": "selftest", "revision": 1},
                "first_event_at": "2026-03-24T10:00:00+08:00",
                "last_event_at": "2026-03-24T10:00:00+08:00",
                "created_at": "2026-03-24T10:00:00+08:00",
                "updated_at": "2026-03-24T10:00:00+08:00",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    threads = run_read(store_root, "list-threads")
    assert_equal(
        [thread["thread_id"] for thread in threads],
        ["thr_late_utc", "thr_early_hk"],
        "list-threads should sort by actual instant across offsets",
    )


def test_context_recorded_at_is_rejected(temp_root: Path) -> None:
    error = expect_failure(
        temp_root / "reject-recorded-at-store",
        "project-turn",
        payload={
            "turn_id": "agent:selftest:reject-recorded-at:0001",
            "user_text": "recorded_at should be rejected",
            "thread": {"thread_id": "thr_reject_recorded_at", "title": "reject", "status": "planned"},
            "context": {"recorded_at": "2026-03-24T10:00:00+08:00"},
        },
    )
    assert_in("context contains unsupported fields: recorded_at", error, "context.recorded_at should be rejected")


def test_jsonl_read_modes(temp_root: Path) -> None:
    compat_store = temp_root / "compat-read-mode-store"
    compat_payload = {
        "turn_id": "agent:selftest:compat-read-mode:0001",
        "user_text": "默认读取模式保持兼容。",
        "assistant_text": "兼容模式继续写入。",
        "thread": {"thread_id": "thr_selftest_compat", "title": "compat", "status": "planned"},
    }
    compat_store.mkdir(parents=True, exist_ok=True)
    (compat_store / "raw_events.jsonl").write_text("{bad json}\n", encoding="utf-8")

    compat_result = run_cli(compat_store, compat_payload, "project-turn")
    assert_equal(compat_result["ok"], True, "compat read mode should keep write path available")
    assert_equal(
        compat_result["recorded_event_ids"],
        ["agent:selftest:compat-read-mode:0001:in", "agent:selftest:compat-read-mode:0001:out"],
        "compat read mode should still record both events",
    )
    assert_equal(len(raw_event_lines(compat_store)), 3, "compat read mode should preserve malformed line plus new events")
    assert_equal(raw_event_lines(compat_store)[0], "{bad json}", "compat read mode should not rewrite malformed line")

    strict_store = temp_root / "strict-read-mode-store"
    strict_payload = {
        "turn_id": "agent:selftest:strict-read-mode:0001",
        "user_text": "严格模式遇坏行应失败。",
        "assistant_text": "不应继续写入。",
        "thread": {"thread_id": "thr_selftest_strict", "title": "strict", "status": "planned"},
    }
    strict_store.mkdir(parents=True, exist_ok=True)
    raw_path = strict_store / "raw_events.jsonl"
    raw_path.write_text("{bad json}\n", encoding="utf-8")

    strict_error = expect_failure(
        strict_store,
        "project-turn",
        "--read-mode",
        "strict",
        payload=strict_payload,
    )
    assert_in("failed to read JSONL", strict_error, "strict read mode should fail with stable prefix")
    assert_in(str(raw_path), strict_error, "strict read mode should report the broken raw_events path")
    assert_in("line 1", strict_error, "strict read mode should include the broken line number")
    assert_in("malformed JSON", strict_error, "strict read mode should report malformed JSON")
    assert_equal(raw_event_lines(strict_store), ["{bad json}"], "strict read mode should not append new raw events")

    history_store = temp_root / "strict-history-read-mode-store"
    thread_id = "thr_selftest_history_strict"
    run_cli(
        history_store,
        {
            "turn_id": "agent:selftest:strict-history-read-mode:0001",
            "user_text": "先创建线程。",
            "thread": {"thread_id": thread_id, "title": "strict-history", "status": "planned"},
        },
        "project-turn",
    )
    run_cli(
        history_store,
        {
            "turn_id": "agent:selftest:strict-history-read-mode:0002",
            "user_text": "再更新线程，生成 history。",
            "thread": {"thread_id": thread_id, "title": "strict-history-2", "status": "done"},
        },
        "project-turn",
    )
    history_path = thread_history_path(history_store, thread_id)
    history_path.write_text("[]\n" + history_path.read_text(encoding="utf-8"), encoding="utf-8")

    compat_history = run_read(
        history_store,
        "list-thread-history",
        "--thread-id",
        thread_id,
        "--read-mode",
        "compat",
    )
    strict_history_error = expect_failure(
        history_store,
        "list-thread-history",
        "--thread-id",
        thread_id,
        "--read-mode",
        "strict",
    )
    assert_equal(len(compat_history), 1, "compat history read should skip non-object line")
    assert_equal(compat_history[0]["title"], "strict-history", "compat history read should preserve valid history entry")
    assert_in("failed to read JSONL", strict_history_error, "strict history read should fail with stable prefix")
    assert_in(str(history_path), strict_history_error, "strict history read should report broken history path")
    assert_in("line 1", strict_history_error, "strict history read should include the broken line number")
    assert_in("expected JSON object", strict_history_error, "strict history read should report non-object line")


def test_existing_thread_inbound_only_recovery_preserves_revision(temp_root: Path) -> None:
    store_root = temp_root / "repair-existing-thread"
    first_payload = {
        "turn_id": "agent:selftest:repair-existing:0001",
        "user_text": "第一次记录线程。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_selftest_existing", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:selftest:repair-existing:0002",
        "user_text": "第二次更新线程。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_selftest_existing", "title": "second", "status": "planned"},
    }

    run_cli(store_root, first_payload, "project-turn")
    template_store = temp_root / "repair-existing-template"
    run_cli(template_store, second_payload, "project-turn")
    append_raw_event(store_root, json.loads(raw_event_lines(template_store)[0]))

    replay = run_cli(store_root, second_payload, "project-turn")
    thread = run_read(store_root, "get-thread", "--thread-id", "thr_selftest_existing")
    history = run_read(store_root, "list-thread-history", "--thread-id", "thr_selftest_existing")

    assert_equal(replay["idempotent_replay"], False, "existing-thread replay should repair missing outbound")
    assert_equal(thread["meta"]["revision"], 2, "existing-thread replay should advance revision to 2")
    assert_equal(
        event_ref_ids(thread),
        [
            "agent:selftest:repair-existing:0001:in",
            "agent:selftest:repair-existing:0001:out",
            "agent:selftest:repair-existing:0002:in",
            "agent:selftest:repair-existing:0002:out",
        ],
        "existing-thread replay should preserve both turns in event refs",
    )
    assert_equal(len(history), 1, "existing-thread replay should append exactly one history entry")
    assert_equal(history[0]["meta"]["revision"], 1, "history entry should keep prior revision")


def test_missing_snapshot_recovery_preserves_multiturn_state(temp_root: Path) -> None:
    store_root = temp_root / "repair-multiturn-snapshot"
    first_payload = {
        "turn_id": "agent:selftest:repair-snapshot:0001",
        "user_text": "第一次记录线程。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_selftest_snapshot", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:selftest:repair-snapshot:0002",
        "user_text": "第二次更新线程。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_selftest_snapshot", "title": "second", "status": "planned"},
    }

    run_cli(store_root, first_payload, "project-turn")
    run_cli(store_root, second_payload, "project-turn")
    thread_before = run_read(store_root, "get-thread", "--thread-id", "thr_selftest_snapshot")
    history_before = run_read(store_root, "list-thread-history", "--thread-id", "thr_selftest_snapshot")
    for path in (store_root / "threads").glob("*.json"):
        path.unlink()

    replay = run_cli(store_root, second_payload, "project-turn")
    thread_after = run_read(store_root, "get-thread", "--thread-id", "thr_selftest_snapshot")
    history_after = run_read(store_root, "list-thread-history", "--thread-id", "thr_selftest_snapshot")

    assert_equal(replay["idempotent_replay"], False, "missing snapshot replay should repair current snapshot")
    assert_equal(
        thread_after["meta"]["revision"],
        thread_before["meta"]["revision"],
        "missing snapshot replay should preserve revision",
    )
    assert_equal(
        thread_after["created_at"],
        thread_before["created_at"],
        "missing snapshot replay should preserve created_at",
    )
    assert_equal(
        thread_after["first_event_at"],
        thread_before["first_event_at"],
        "missing snapshot replay should preserve first_event_at",
    )
    assert_equal(
        event_ref_ids(thread_after),
        event_ref_ids(thread_before),
        "missing snapshot replay should preserve historical event refs",
    )
    assert_equal(history_after, history_before, "missing snapshot replay should not duplicate history entries")


def test_prepared_txn_recovery_remains_idempotent(temp_root: Path) -> None:
    store_root = temp_root / "txn-prepared-repeat-store"
    payload = {
        "turn_id": "agent:selftest:txn:prepared-repeat:0001",
        "user_text": "prepared 阶段重复恢复。",
        "assistant_text": "继续完成提交。",
        "thread": {
            "thread_id": "thr_selftest_txn_prepared_repeat",
            "title": "prepared-repeat",
            "status": "planned",
        },
    }

    template_store = temp_root / "txn-prepared-repeat-template"
    run_cli(template_store, payload, "project-turn")
    template_records = raw_event_records_for_turn(template_store, payload["turn_id"])
    fingerprint = template_records[0]["payload"][TIMELINE_META_KEY]["fingerprint"]
    recorded_at = template_records[0]["recorded_at"]

    txn_path = write_project_turn_txn(
        store_root,
        payload["turn_id"],
        {
            "turn_id": payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": "prepared",
            "recorded_at": recorded_at,
            "thread_id": "thr_selftest_txn_prepared_repeat",
            "required_event_ids": [f"{payload['turn_id']}:in", f"{payload['turn_id']}:out"],
            "has_thread": True,
            "baseline_thread": None,
            "target_snapshot": None,
            "history_entry": None,
        },
    )

    recovery = run_cli(store_root, payload, "project-turn")
    replay = run_cli(store_root, payload, "project-turn")
    thread = run_read(store_root, "get-thread", "--thread-id", "thr_selftest_txn_prepared_repeat")
    history = run_read(store_root, "list-thread-history", "--thread-id", "thr_selftest_txn_prepared_repeat")

    assert_equal(recovery["idempotent_replay"], False, "prepared recovery should finish the transaction")
    assert_equal(replay["idempotent_replay"], True, "second replay should be idempotent after prepared recovery")
    assert_equal(
        recovery["recorded_event_ids"],
        [f"{payload['turn_id']}:in", f"{payload['turn_id']}:out"],
        "prepared recovery should record both raw events exactly once",
    )
    assert_equal(thread["meta"]["revision"], 1, "prepared recovery should create revision 1 thread")
    assert_equal(len(history), 0, "prepared recovery should not append history for first snapshot")
    assert_equal(len(raw_event_lines(store_root)), 2, "prepared recovery should not duplicate raw events")
    assert_equal(txn_path.exists(), False, "prepared recovery should delete txn file")


def test_history_committed_txn_recovery_remains_idempotent(temp_root: Path) -> None:
    store_root = temp_root / "txn-history-repeat-store"
    first_payload = {
        "turn_id": "agent:selftest:txn:history-repeat:0001",
        "user_text": "第一次记录。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_selftest_txn_history_repeat", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:selftest:txn:history-repeat:0002",
        "user_text": "第二次记录。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_selftest_txn_history_repeat", "title": "second", "status": "planned"},
    }

    run_cli(store_root, first_payload, "project-turn")
    baseline_thread = run_read(store_root, "get-thread", "--thread-id", "thr_selftest_txn_history_repeat")

    template_store = temp_root / "txn-history-repeat-template"
    run_cli(template_store, first_payload, "project-turn")
    run_cli(template_store, second_payload, "project-turn")
    template_records = raw_event_records_for_turn(template_store, second_payload["turn_id"])
    fingerprint = template_records[0]["payload"][TIMELINE_META_KEY]["fingerprint"]
    recorded_at = template_records[0]["recorded_at"]
    target_snapshot = run_read(template_store, "get-thread", "--thread-id", "thr_selftest_txn_history_repeat")

    for record in template_records:
        append_raw_event(store_root, record)
    thread_snapshot_path(store_root, "thr_selftest_txn_history_repeat").parent.mkdir(parents=True, exist_ok=True)
    thread_snapshot_path(store_root, "thr_selftest_txn_history_repeat").write_text(
        json.dumps(target_snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    history_path = thread_history_path(store_root, "thr_selftest_txn_history_repeat")
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(baseline_thread, ensure_ascii=False) + "\n", encoding="utf-8")

    txn_path = write_project_turn_txn(
        store_root,
        second_payload["turn_id"],
        {
            "turn_id": second_payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": "history_committed",
            "recorded_at": recorded_at,
            "thread_id": "thr_selftest_txn_history_repeat",
            "required_event_ids": [f"{second_payload['turn_id']}:in", f"{second_payload['turn_id']}:out"],
            "has_thread": True,
            "baseline_thread": baseline_thread,
            "target_snapshot": target_snapshot,
            "history_entry": baseline_thread,
        },
    )

    recovery = run_cli(store_root, second_payload, "project-turn")
    replay = run_cli(store_root, second_payload, "project-turn")
    thread = run_read(store_root, "get-thread", "--thread-id", "thr_selftest_txn_history_repeat")
    history = run_read(store_root, "list-thread-history", "--thread-id", "thr_selftest_txn_history_repeat")

    assert_equal(recovery["idempotent_replay"], False, "history-committed recovery should finish the transaction")
    assert_equal(replay["idempotent_replay"], True, "second replay should be idempotent after txn cleanup")
    assert_equal(thread["meta"]["revision"], 2, "history-committed recovery should preserve revision 2")
    assert_equal(len(history), 1, "history-committed recovery should not duplicate history")
    assert_equal(history[0]["meta"]["revision"], 1, "history entry should keep prior revision")
    assert_equal(len(raw_event_lines(store_root)), 4, "history-committed recovery should not duplicate raw events")
    assert_equal(txn_path.exists(), False, "history-committed recovery should delete txn file")


def test_snapshot_committed_txn_recovery_remains_idempotent(temp_root: Path) -> None:
    store_root = temp_root / "txn-snapshot-repeat-store"
    first_payload = {
        "turn_id": "agent:selftest:txn:snapshot-repeat:0001",
        "user_text": "第一次记录。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_selftest_txn_snapshot_repeat", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:selftest:txn:snapshot-repeat:0002",
        "user_text": "第二次记录。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_selftest_txn_snapshot_repeat", "title": "second", "status": "planned"},
    }

    run_cli(store_root, first_payload, "project-turn")
    baseline_thread = run_read(store_root, "get-thread", "--thread-id", "thr_selftest_txn_snapshot_repeat")

    template_store = temp_root / "txn-snapshot-repeat-template"
    run_cli(template_store, first_payload, "project-turn")
    run_cli(template_store, second_payload, "project-turn")
    template_records = raw_event_records_for_turn(template_store, second_payload["turn_id"])
    fingerprint = template_records[0]["payload"][TIMELINE_META_KEY]["fingerprint"]
    recorded_at = template_records[0]["recorded_at"]
    target_snapshot = run_read(template_store, "get-thread", "--thread-id", "thr_selftest_txn_snapshot_repeat")

    for record in template_records:
        append_raw_event(store_root, record)
    thread_snapshot_path(store_root, "thr_selftest_txn_snapshot_repeat").parent.mkdir(parents=True, exist_ok=True)
    thread_snapshot_path(store_root, "thr_selftest_txn_snapshot_repeat").write_text(
        json.dumps(target_snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    txn_path = write_project_turn_txn(
        store_root,
        second_payload["turn_id"],
        {
            "turn_id": second_payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": "snapshot_committed",
            "recorded_at": recorded_at,
            "thread_id": "thr_selftest_txn_snapshot_repeat",
            "required_event_ids": [f"{second_payload['turn_id']}:in", f"{second_payload['turn_id']}:out"],
            "has_thread": True,
            "baseline_thread": baseline_thread,
            "target_snapshot": target_snapshot,
            "history_entry": baseline_thread,
        },
    )

    recovery = run_cli(store_root, second_payload, "project-turn")
    replay = run_cli(store_root, second_payload, "project-turn")
    thread = run_read(store_root, "get-thread", "--thread-id", "thr_selftest_txn_snapshot_repeat")
    history = run_read(store_root, "list-thread-history", "--thread-id", "thr_selftest_txn_snapshot_repeat")

    assert_equal(recovery["idempotent_replay"], False, "snapshot-committed recovery should finish the transaction")
    assert_equal(replay["idempotent_replay"], True, "second replay should be idempotent after snapshot-committed recovery")
    assert_equal(thread["meta"]["revision"], 2, "snapshot-committed recovery should preserve revision 2")
    assert_equal(len(history), 1, "snapshot-committed recovery should not duplicate history")
    assert_equal(history[0]["meta"]["revision"], 1, "history entry should keep prior revision")
    assert_equal(len(raw_event_lines(store_root)), 4, "snapshot-committed recovery should not duplicate raw events")
    assert_equal(txn_path.exists(), False, "snapshot-committed recovery should delete txn file")


def main() -> int:
    temp_root = ROOT / "tmp" / "selftest-run"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_root.mkdir(parents=True, exist_ok=True)
    try:
        test_regression_basics(temp_root)
        test_thread_id_path_isolation(temp_root)
        test_thread_path_case_insensitive_safety(temp_root)
        test_implicit_thread_id_is_stable_and_collision_free(temp_root)
        test_source_normalization_and_partial_write_recovery(temp_root)
        test_list_threads_orders_by_absolute_time(temp_root)
        test_context_recorded_at_is_rejected(temp_root)
        test_jsonl_read_modes(temp_root)
        test_existing_thread_inbound_only_recovery_preserves_revision(temp_root)
        test_missing_snapshot_recovery_preserves_multiturn_state(temp_root)
        test_prepared_txn_recovery_remains_idempotent(temp_root)
        test_snapshot_committed_txn_recovery_remains_idempotent(temp_root)
        test_history_committed_txn_recovery_remains_idempotent(temp_root)
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root)

    print("timeline-memory selftest passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
