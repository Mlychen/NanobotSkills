from __future__ import annotations

import json
from pathlib import Path


def _raw_event_lines(store_root: Path) -> list[str]:
    raw_path = store_root / "raw_events.jsonl"
    if not raw_path.exists():
        return []
    return [line for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _raw_events(store_root: Path) -> list[dict]:
    return [json.loads(line) for line in _raw_event_lines(store_root)]


def _thread_snapshot_path(store_root: Path, thread_id: str) -> Path:
    return store_root / "threads" / f"tid_{thread_id.encode('utf-8').hex()}.json"


def test_project_turn_idempotent_replay_and_query_surface(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "timeline-store"
    payload = {
        "turn_id": "agent:e2e:0001",
        "user_text": "下周三下午三点交电费。",
        "assistant_text": "记下了，下周三下午三点交电费。",
        "thread": {
            "thread_id": "thr_pay_bill",
            "title": "交电费",
            "status": "planned",
            "plan_time": {"due_at": "2026-03-25T15:00:00+08:00"},
        },
    }

    create = cli_runner.run_json(store_root, "project-turn", payload=payload)
    replay = cli_runner.run_json(store_root, "project-turn", payload=payload)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_pay_bill"])
    threads = cli_runner.run_json(store_root, "list-threads", args=["--status", "planned"])
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_pay_bill"])

    assert create["recorded_event_ids"] == ["agent:e2e:0001:in", "agent:e2e:0001:out"]
    assert replay["idempotent_replay"] is True
    assert len(_raw_event_lines(store_root)) == 2
    assert thread["title"] == "交电费"
    assert len(threads) == 1
    assert history == []


def test_thread_update_increments_revision_and_history(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "update-store"
    create_payload = {
        "turn_id": "agent:e2e:update:0001",
        "user_text": "周三三点交电费。",
        "assistant_text": "已记录。",
        "thread": {
            "thread_id": "thr_update_bill",
            "title": "交电费",
            "status": "planned",
            "plan_time": {"due_at": "2026-03-25T15:00:00+08:00"},
        },
    }
    update_payload = {
        "turn_id": "agent:e2e:update:0002",
        "user_text": "改成四点交电费。",
        "assistant_text": "已更新到四点。",
        "thread": {
            "thread_id": "thr_update_bill",
            "title": "交电费",
            "status": "planned",
            "plan_time": {"due_at": "2026-03-25T16:00:00+08:00"},
        },
    }

    cli_runner.run_json(store_root, "project-turn", payload=create_payload)
    update = cli_runner.run_json(store_root, "project-turn", payload=update_payload)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_update_bill"])
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_update_bill"])

    assert update["thread"]["meta"]["revision"] == 2
    assert thread["plan_time"]["due_at"] == "2026-03-25T16:00:00+08:00"
    assert len(history) == 1
    assert history[0]["plan_time"]["due_at"] == "2026-03-25T15:00:00+08:00"


def test_thread_id_special_character_isolation(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "collision-store"
    cli_runner.run_json(
        store_root,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:collision:0001",
            "user_text": "记录 slash 线程。",
            "thread": {"thread_id": "thr/a", "title": "slash", "status": "planned"},
        },
    )
    cli_runner.run_json(
        store_root,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:collision:0002",
            "user_text": "记录 underscore 线程。",
            "thread": {"thread_id": "thr_a", "title": "underscore", "status": "done"},
        },
    )
    cli_runner.run_json(
        store_root,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:collision:0003",
            "user_text": "更新 slash 线程。",
            "thread": {"thread_id": "thr/a", "title": "slash-updated", "status": "planned"},
        },
    )

    slash = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr/a"])
    underscore = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_a"])
    slash_history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr/a"])
    underscore_history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_a"])
    threads = cli_runner.run_json(store_root, "list-threads")

    assert slash["title"] == "slash-updated"
    assert underscore["title"] == "underscore"
    assert len(slash_history) == 1
    assert len(underscore_history) == 0
    assert len(threads) == 2


def test_implicit_thread_id_stable_and_collision_free(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "derived-thread-id-store"
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

    first = cli_runner.run_json(store_root, "project-turn", payload=first_payload)
    second = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    threads = cli_runner.run_json(store_root, "list-threads")

    expected_first_thread_id = f"thr_{first_payload['turn_id'].encode('utf-8').hex()}"
    expected_second_thread_id = f"thr_{second_payload['turn_id'].encode('utf-8').hex()}"

    assert first["thread"]["thread_id"] == expected_first_thread_id
    assert second["thread"]["thread_id"] == expected_second_thread_id
    assert sorted(thread["thread_id"] for thread in threads) == sorted(
        [expected_first_thread_id, expected_second_thread_id]
    )


def test_source_normalization_and_partial_write_recovery(cli_runner, scratch_root: Path) -> None:
    source_store = scratch_root / "source-store"
    source_result = cli_runner.run_json(
        source_store,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:source:0001",
            "user_text": "source 为空字符串。",
            "thread": {"thread_id": "thr_source", "title": "source", "status": "planned"},
            "context": {"source": ""},
        },
    )
    raw_event = _raw_events(source_store)[0]

    assert raw_event["source"] == "skill://timeline-memory"
    assert source_result["thread"]["event_refs"][0]["added_by"] == "skill://timeline-memory"
    assert source_result["thread"]["meta"]["created_by"] == "skill://timeline-memory"

    payload = {
        "turn_id": "agent:e2e:repair:0001",
        "user_text": "先写 inbound，再补齐。",
        "assistant_text": "补齐 outbound。",
        "thread": {"thread_id": "thr_repair", "title": "repair", "status": "planned"},
    }
    template_store = scratch_root / "repair-template"
    cli_runner.run_json(template_store, "project-turn", payload=payload)
    inbound_line = _raw_event_lines(template_store)[0]

    inbound_only_store = scratch_root / "repair-inbound"
    inbound_only_store.mkdir(parents=True, exist_ok=True)
    (inbound_only_store / "raw_events.jsonl").write_text(f"{inbound_line}\n", encoding="utf-8")
    inbound_recovery = cli_runner.run_json(inbound_only_store, "project-turn", payload=payload)
    repaired_thread = cli_runner.run_json(inbound_only_store, "get-thread", args=["--thread-id", "thr_repair"])

    assert inbound_recovery["idempotent_replay"] is False
    assert inbound_recovery["recorded_event_ids"] == ["agent:e2e:repair:0001:in", "agent:e2e:repair:0001:out"]
    assert len(_raw_event_lines(inbound_only_store)) == 2
    assert repaired_thread["title"] == "repair"

    missing_snapshot_store = scratch_root / "repair-missing-thread"
    missing_payload = {
        "turn_id": "agent:e2e:repair:0002",
        "user_text": "raw events 存在，补写 thread。",
        "assistant_text": "补写完成。",
        "thread": {"thread_id": "thr_repair_snapshot", "title": "repair-thread", "status": "planned"},
    }
    cli_runner.run_json(missing_snapshot_store, "project-turn", payload=missing_payload)
    for path in (missing_snapshot_store / "threads").glob("*.json"):
        path.unlink()
    missing_snapshot_replay = cli_runner.run_json(missing_snapshot_store, "project-turn", payload=missing_payload)

    assert missing_snapshot_replay["idempotent_replay"] is False
    assert missing_snapshot_replay["thread"]["title"] == "repair-thread"
    assert len(_raw_event_lines(missing_snapshot_store)) == 2


def test_list_threads_orders_by_absolute_time_across_offsets(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "mixed-offset-order-store"
    threads_dir = store_root / "threads"
    threads_dir.mkdir(parents=True, exist_ok=True)
    _thread_snapshot_path(store_root, "thr_late_utc").write_text(
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
                "meta": {"created_by": "test", "updated_by": "test", "revision": 1},
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
    _thread_snapshot_path(store_root, "thr_early_hk").write_text(
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
                "meta": {"created_by": "test", "updated_by": "test", "revision": 1},
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

    threads = cli_runner.run_json(store_root, "list-threads")

    assert [thread["thread_id"] for thread in threads] == ["thr_late_utc", "thr_early_hk"]


def test_project_turn_rejects_context_recorded_at(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "reject-recorded-at-store"
    error = cli_runner.expect_failure(
        store_root,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:reject-recorded-at:0001",
            "user_text": "不应接受 recorded_at。",
            "thread": {"thread_id": "thr_reject_recorded_at", "title": "reject", "status": "planned"},
            "context": {"recorded_at": "2026-03-24T10:00:00+08:00"},
        },
    )

    assert "context contains unsupported fields: recorded_at" in error


def test_turn_id_conflict_returns_error(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "conflict-store"
    cli_runner.run_json(
        store_root,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:conflict:0001",
            "user_text": "旧内容",
            "thread": {"thread_id": "thr_conflict", "title": "conflict", "status": "planned"},
        },
    )
    error = cli_runner.expect_failure(
        store_root,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:conflict:0001",
            "user_text": "新内容",
            "thread": {"thread_id": "thr_conflict", "title": "conflict", "status": "planned"},
        },
    )

    assert "different payload already recorded" in error
