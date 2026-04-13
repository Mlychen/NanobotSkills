from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from scripts.models import ProjectTurnInput
from scripts.timeline_cli import build_raw_event, resolve_effective_source, resolve_thread_id


def _raw_event_lines(store_root: Path) -> list[str]:
    raw_path = store_root / "raw_events.jsonl"
    if not raw_path.exists():
        return []
    return [line for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _raw_events(store_root: Path) -> list[dict]:
    return [json.loads(line) for line in _raw_event_lines(store_root)]


def _thread_snapshot_path(store_root: Path, thread_id: str) -> Path:
    return store_root / "threads" / f"tid_{thread_id.encode('utf-8').hex()}.json"


def _read_thread_snapshot(store_root: Path, thread_id: str) -> dict:
    return json.loads(_thread_snapshot_path(store_root, thread_id).read_text(encoding="utf-8"))


def _event_ref_ids(thread_payload: dict) -> list[str]:
    return [ref["event_id"] for ref in thread_payload["event_refs"]]


def _turn_event_refs(thread_payload: dict, turn_id: str) -> list[dict]:
    prefix = f"{turn_id}:"
    return [ref for ref in thread_payload["event_refs"] if str(ref["event_id"]).startswith(prefix)]


def _normalized_event_refs(thread_payload: dict) -> list[tuple[str, str, str, object]]:
    return [
        (
            str(ref["event_id"]),
            str(ref["role"]),
            str(ref["added_by"]),
            ref.get("confidence"),
        )
        for ref in thread_payload["event_refs"]
    ]


def _thread_history_path(store_root: Path, thread_id: str) -> Path:
    return store_root / "thread_history" / f"tid_{thread_id.encode('utf-8').hex()}.jsonl"


def _read_thread_history(store_root: Path, thread_id: str) -> list[dict]:
    path = _thread_history_path(store_root, thread_id)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _project_turn_txn_path(store_root: Path, turn_id: str) -> Path:
    return store_root / "_txn" / "project_turn" / f"turn_{turn_id.encode('utf-8').hex()}.json"


def _write_project_turn_txn(store_root: Path, turn_id: str, payload: dict) -> Path:
    path = _project_turn_txn_path(store_root, turn_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _required_turn_event_ids(turn_id: str, *, has_outbound: bool) -> list[str]:
    event_ids = [f"{turn_id}:in"]
    if has_outbound:
        event_ids.append(f"{turn_id}:out")
    return event_ids


def _turn_raw_events(store_root: Path, turn_id: str) -> list[dict]:
    prefix = f"{turn_id}:"
    return [record for record in _raw_events(store_root) if str(record["event_id"]).startswith(prefix)]


def _append_raw_records(store_root: Path, records: list[dict]) -> None:
    raw_path = store_root / "raw_events.jsonl"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    with open(raw_path, "a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def _write_thread_snapshot(store_root: Path, thread_id: str, payload: dict) -> None:
    path = _thread_snapshot_path(store_root, thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_thread_history(store_root: Path, thread_id: str, records: list[dict]) -> None:
    path = _thread_history_path(store_root, thread_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def _thread_snapshot_payload(
    *,
    thread_id: str,
    title: str,
    status: str = "planned",
    thread_kind: str = "task",
    last_event_at: str | None,
    updated_at: str,
) -> dict:
    return {
        "thread_id": thread_id,
        "thread_kind": thread_kind,
        "title": title,
        "status": status,
        "plan_time": {},
        "fact_time": {},
        "content": {},
        "event_refs": [],
        "meta": {"created_by": "test", "updated_by": "test", "revision": 1},
        "first_event_at": last_event_at,
        "last_event_at": last_event_at,
        "created_at": updated_at,
        "updated_at": updated_at,
    }


def _build_inbound_raw_line(payload: dict, *, recorded_at: str = "2026-03-24T00:00:00+08:00") -> str:
    turn_input = ProjectTurnInput.from_dict(payload)
    record = build_raw_event(
        turn_input=turn_input,
        role="inbound",
        recorded_at=recorded_at,
        fingerprint=turn_input.fingerprint(),
        thread_id=resolve_thread_id(turn_input),
        source=resolve_effective_source(turn_input),
    )
    return json.dumps(record.to_dict(), ensure_ascii=False)


def _assert_turn_state_matches_reference(
    *,
    expected_store: Path,
    actual_store: Path,
    thread_id: str,
    turn_id: str,
) -> None:
    assert _turn_raw_events(actual_store, turn_id) == _turn_raw_events(expected_store, turn_id)
    actual_thread = _read_thread_snapshot(actual_store, thread_id)
    expected_thread = _read_thread_snapshot(expected_store, thread_id)
    assert actual_thread["title"] == expected_thread["title"]
    assert actual_thread["status"] == expected_thread["status"]
    assert actual_thread["content"] == expected_thread["content"]
    assert actual_thread["plan_time"] == expected_thread["plan_time"]
    assert actual_thread["fact_time"] == expected_thread["fact_time"]
    assert actual_thread["meta"]["revision"] == expected_thread["meta"]["revision"]
    assert _normalized_event_refs(actual_thread) == _normalized_event_refs(expected_thread)

    actual_history = _read_thread_history(actual_store, thread_id)
    expected_history = _read_thread_history(expected_store, thread_id)
    assert len(actual_history) == len(expected_history)
    for actual_entry, expected_entry in zip(actual_history, expected_history):
        assert actual_entry["title"] == expected_entry["title"]
        assert actual_entry["status"] == expected_entry["status"]
        assert actual_entry["content"] == expected_entry["content"]
        assert actual_entry["plan_time"] == expected_entry["plan_time"]
        assert actual_entry["fact_time"] == expected_entry["fact_time"]
        assert actual_entry["meta"]["revision"] == expected_entry["meta"]["revision"]
        assert _normalized_event_refs(actual_entry) == _normalized_event_refs(expected_entry)


def _assert_turn_semantics(
    *,
    raw_events: list[dict],
    thread_payload: dict | None,
    turn_id: str,
    has_outbound: bool,
) -> None:
    inbound_id = f"{turn_id}:in"
    outbound_id = f"{turn_id}:out"
    expected_event_ids = [inbound_id, outbound_id] if has_outbound else [inbound_id]

    assert [record["event_id"] for record in raw_events] == expected_event_ids
    inbound = raw_events[0]
    assert inbound["correlation_id"] == turn_id
    assert inbound["causation_id"] is None
    assert inbound["confidence"] is None

    if has_outbound:
        outbound = raw_events[1]
        assert outbound["correlation_id"] == turn_id
        assert outbound["causation_id"] == inbound_id
        assert outbound["confidence"] is None

    if thread_payload is None:
        return

    turn_event_refs = _turn_event_refs(thread_payload, turn_id)
    assert [ref["event_id"] for ref in turn_event_refs] == expected_event_ids
    assert [ref["role"] for ref in turn_event_refs] == (["primary", "context"] if has_outbound else ["primary"])
    assert all(ref["confidence"] is None for ref in turn_event_refs)


def _resolve_python_command() -> list[str]:
    if sys.executable and Path(sys.executable).exists():
        return [sys.executable]
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("current python executable is unavailable and uv not found on PATH")
    return [uv, "run", "python"]


def _real_cli_env(tmp_root: Path, *, extra_env: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env["TIMELINE_TEST_MODE"] = "sandbox-safe"
    env["TIMELINE_TEST_TMP_ROOT"] = str(tmp_root)
    env["TMP"] = str(tmp_root)
    env["TEMP"] = str(tmp_root)
    env["TMPDIR"] = str(tmp_root)
    if extra_env:
        env.update(extra_env)
    return env


def _start_real_project_turn(
    repo_root: Path,
    cli_path: Path,
    tmp_root: Path,
    store_root: Path,
    *,
    payload: dict,
    input_name: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    input_path = store_root.parent / input_name
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    command = [
        *_resolve_python_command(),
        str(cli_path),
        "project-turn",
        "--store-root",
        str(store_root),
        "--input",
        str(input_path),
    ]
    return subprocess.Popen(
        command,
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_real_cli_env(tmp_root, extra_env=extra_env),
    )


def _wait_for_lock_owner(store_root: Path, turn_id: str, *, timeout_seconds: float = 2.0) -> None:
    lock_path = store_root / "_locks" / "project_turn.lock"
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if lock_path.exists():
            try:
                payload = json.loads(lock_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = None
            if isinstance(payload, dict) and payload.get("turn_id") == turn_id:
                return
        time.sleep(0.02)
    raise AssertionError(f"project-turn lock was not acquired by {turn_id}")


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
    _assert_turn_semantics(
        raw_events=_turn_raw_events(store_root, payload["turn_id"]),
        thread_payload=thread,
        turn_id=payload["turn_id"],
        has_outbound=True,
    )
    assert not _project_turn_txn_path(store_root, payload["turn_id"]).exists()


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


def test_project_turn_recovers_from_prepared_txn_stage(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "txn-prepared-store"
    payload = {
        "turn_id": "agent:e2e:txn:prepared:0001",
        "user_text": "prepared 阶段恢复。",
        "assistant_text": "继续完成提交。",
        "thread": {"thread_id": "thr_txn_prepared", "title": "prepared", "status": "planned"},
    }
    template_store = scratch_root / "txn-prepared-template"
    cli_runner.run_json(template_store, "project-turn", payload=payload)
    template_events = _turn_raw_events(template_store, payload["turn_id"])
    fingerprint = template_events[0]["payload"]["_timeline_memory"]["fingerprint"]
    recorded_at = template_events[0]["recorded_at"]

    txn_path = _write_project_turn_txn(
        store_root,
        payload["turn_id"],
        {
            "turn_id": payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": "prepared",
            "recorded_at": recorded_at,
            "thread_id": "thr_txn_prepared",
            "required_event_ids": _required_turn_event_ids(payload["turn_id"], has_outbound=True),
            "has_thread": True,
            "baseline_thread": None,
            "target_snapshot": None,
            "history_entry": None,
        },
    )

    result = cli_runner.run_json(store_root, "project-turn", payload=payload)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_prepared"])

    assert result["idempotent_replay"] is False
    assert result["recorded_event_ids"] == _required_turn_event_ids(payload["turn_id"], has_outbound=True)
    assert len(_turn_raw_events(store_root, payload["turn_id"])) == 2
    assert thread["title"] == "prepared"
    _assert_turn_semantics(
        raw_events=_turn_raw_events(store_root, payload["turn_id"]),
        thread_payload=thread,
        turn_id=payload["turn_id"],
        has_outbound=True,
    )
    assert not txn_path.exists()


def test_project_turn_repeated_recovery_from_prepared_txn_creates_no_history_on_first_snapshot(
    cli_runner, scratch_root: Path
) -> None:
    store_root = scratch_root / "txn-prepared-repeat-store"
    payload = {
        "turn_id": "agent:e2e:txn:prepared-repeat:0001",
        "user_text": "prepared 阶段重复恢复。",
        "assistant_text": "继续完成提交。",
        "thread": {"thread_id": "thr_txn_prepared_repeat", "title": "prepared-repeat", "status": "planned"},
    }
    template_store = scratch_root / "txn-prepared-repeat-template"
    cli_runner.run_json(template_store, "project-turn", payload=payload)
    template_events = _turn_raw_events(template_store, payload["turn_id"])
    fingerprint = template_events[0]["payload"]["_timeline_memory"]["fingerprint"]
    recorded_at = template_events[0]["recorded_at"]

    txn_path = _write_project_turn_txn(
        store_root,
        payload["turn_id"],
        {
            "turn_id": payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": "prepared",
            "recorded_at": recorded_at,
            "thread_id": "thr_txn_prepared_repeat",
            "required_event_ids": _required_turn_event_ids(payload["turn_id"], has_outbound=True),
            "has_thread": True,
            "baseline_thread": None,
            "target_snapshot": None,
            "history_entry": None,
        },
    )

    recovery = cli_runner.run_json(store_root, "project-turn", payload=payload)
    replay = cli_runner.run_json(store_root, "project-turn", payload=payload)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_prepared_repeat"])
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_txn_prepared_repeat"])

    assert recovery["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert recovery["recorded_event_ids"] == _required_turn_event_ids(payload["turn_id"], has_outbound=True)
    assert len(_turn_raw_events(store_root, payload["turn_id"])) == 2
    assert thread["title"] == "prepared-repeat"
    assert thread["meta"]["revision"] == 1
    assert len(history) == 0
    assert not txn_path.exists()


def test_project_turn_recovers_from_raw_committed_txn_stage(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "txn-raw-committed-store"
    first_payload = {
        "turn_id": "agent:e2e:txn:raw:0001",
        "user_text": "第一次记录。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_txn_raw", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:e2e:txn:raw:0002",
        "user_text": "第二次记录。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_txn_raw", "title": "second", "status": "planned"},
    }
    cli_runner.run_json(store_root, "project-turn", payload=first_payload)
    baseline_thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_raw"])

    template_store = scratch_root / "txn-raw-committed-template"
    cli_runner.run_json(template_store, "project-turn", payload=first_payload)
    cli_runner.run_json(template_store, "project-turn", payload=second_payload)
    template_events = _turn_raw_events(template_store, second_payload["turn_id"])
    fingerprint = template_events[0]["payload"]["_timeline_memory"]["fingerprint"]
    recorded_at = template_events[0]["recorded_at"]

    raw_path = store_root / "raw_events.jsonl"
    with open(raw_path, "a", encoding="utf-8") as handle:
        for record in template_events:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    txn_path = _write_project_turn_txn(
        store_root,
        second_payload["turn_id"],
        {
            "turn_id": second_payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": "raw_committed",
            "recorded_at": recorded_at,
            "thread_id": "thr_txn_raw",
            "required_event_ids": _required_turn_event_ids(second_payload["turn_id"], has_outbound=True),
            "has_thread": True,
            "baseline_thread": baseline_thread,
            "target_snapshot": None,
            "history_entry": None,
        },
    )

    result = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_raw"])
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_txn_raw"])

    assert result["idempotent_replay"] is False
    assert thread["title"] == "second"
    assert thread["meta"]["revision"] == 2
    assert len(history) == 1
    assert history[0]["meta"]["revision"] == 1
    assert not txn_path.exists()


def test_project_turn_repeated_recovery_from_raw_committed_txn_keeps_single_history_entry(
    cli_runner, scratch_root: Path
) -> None:
    store_root = scratch_root / "txn-raw-repeat-store"
    first_payload = {
        "turn_id": "agent:e2e:txn:raw-repeat:0001",
        "user_text": "第一次记录。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_txn_raw_repeat", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:e2e:txn:raw-repeat:0002",
        "user_text": "第二次记录。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_txn_raw_repeat", "title": "second", "status": "planned"},
    }
    cli_runner.run_json(store_root, "project-turn", payload=first_payload)
    baseline_thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_raw_repeat"])

    template_store = scratch_root / "txn-raw-repeat-template"
    cli_runner.run_json(template_store, "project-turn", payload=first_payload)
    cli_runner.run_json(template_store, "project-turn", payload=second_payload)
    template_events = _turn_raw_events(template_store, second_payload["turn_id"])
    fingerprint = template_events[0]["payload"]["_timeline_memory"]["fingerprint"]
    recorded_at = template_events[0]["recorded_at"]

    raw_path = store_root / "raw_events.jsonl"
    with open(raw_path, "a", encoding="utf-8") as handle:
        for record in template_events:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    txn_path = _write_project_turn_txn(
        store_root,
        second_payload["turn_id"],
        {
            "turn_id": second_payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": "raw_committed",
            "recorded_at": recorded_at,
            "thread_id": "thr_txn_raw_repeat",
            "required_event_ids": _required_turn_event_ids(second_payload["turn_id"], has_outbound=True),
            "has_thread": True,
            "baseline_thread": baseline_thread,
            "target_snapshot": None,
            "history_entry": None,
        },
    )

    recovery = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    replay = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_raw_repeat"])
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_txn_raw_repeat"])

    assert recovery["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert thread["title"] == "second"
    assert thread["meta"]["revision"] == 2
    assert len(history) == 1
    assert history[0]["meta"]["revision"] == 1
    assert len(_turn_raw_events(store_root, second_payload["turn_id"])) == 2
    assert not txn_path.exists()


def test_project_turn_recovers_from_snapshot_committed_txn_stage(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "txn-snapshot-committed-store"
    first_payload = {
        "turn_id": "agent:e2e:txn:snapshot:0001",
        "user_text": "第一次记录。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_txn_snapshot", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:e2e:txn:snapshot:0002",
        "user_text": "第二次记录。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_txn_snapshot", "title": "second", "status": "planned"},
    }
    cli_runner.run_json(store_root, "project-turn", payload=first_payload)
    baseline_thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_snapshot"])

    template_store = scratch_root / "txn-snapshot-committed-template"
    cli_runner.run_json(template_store, "project-turn", payload=first_payload)
    cli_runner.run_json(template_store, "project-turn", payload=second_payload)
    template_events = _turn_raw_events(template_store, second_payload["turn_id"])
    fingerprint = template_events[0]["payload"]["_timeline_memory"]["fingerprint"]
    recorded_at = template_events[0]["recorded_at"]
    target_snapshot = cli_runner.run_json(template_store, "get-thread", args=["--thread-id", "thr_txn_snapshot"])

    raw_path = store_root / "raw_events.jsonl"
    with open(raw_path, "a", encoding="utf-8") as handle:
        for record in template_events:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    _thread_snapshot_path(store_root, "thr_txn_snapshot").write_text(
        json.dumps(target_snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    txn_path = _write_project_turn_txn(
        store_root,
        second_payload["turn_id"],
        {
            "turn_id": second_payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": "snapshot_committed",
            "recorded_at": recorded_at,
            "thread_id": "thr_txn_snapshot",
            "required_event_ids": _required_turn_event_ids(second_payload["turn_id"], has_outbound=True),
            "has_thread": True,
            "baseline_thread": baseline_thread,
            "target_snapshot": target_snapshot,
            "history_entry": baseline_thread,
        },
    )

    result = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_snapshot"])
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_txn_snapshot"])

    assert result["idempotent_replay"] is False
    assert thread["title"] == "second"
    assert thread["meta"]["revision"] == 2
    assert len(history) == 1
    assert history[0]["meta"]["revision"] == 1
    assert not txn_path.exists()


def test_project_turn_repeated_recovery_from_snapshot_committed_txn_keeps_single_history_entry(
    cli_runner, scratch_root: Path
) -> None:
    store_root = scratch_root / "txn-snapshot-repeat-store"
    first_payload = {
        "turn_id": "agent:e2e:txn:snapshot-repeat:0001",
        "user_text": "第一次记录。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_txn_snapshot_repeat", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:e2e:txn:snapshot-repeat:0002",
        "user_text": "第二次记录。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_txn_snapshot_repeat", "title": "second", "status": "planned"},
    }
    cli_runner.run_json(store_root, "project-turn", payload=first_payload)
    baseline_thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_snapshot_repeat"])

    template_store = scratch_root / "txn-snapshot-repeat-template"
    cli_runner.run_json(template_store, "project-turn", payload=first_payload)
    cli_runner.run_json(template_store, "project-turn", payload=second_payload)
    template_events = _turn_raw_events(template_store, second_payload["turn_id"])
    fingerprint = template_events[0]["payload"]["_timeline_memory"]["fingerprint"]
    recorded_at = template_events[0]["recorded_at"]
    target_snapshot = cli_runner.run_json(template_store, "get-thread", args=["--thread-id", "thr_txn_snapshot_repeat"])

    raw_path = store_root / "raw_events.jsonl"
    with open(raw_path, "a", encoding="utf-8") as handle:
        for record in template_events:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    _thread_snapshot_path(store_root, "thr_txn_snapshot_repeat").write_text(
        json.dumps(target_snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    txn_path = _write_project_turn_txn(
        store_root,
        second_payload["turn_id"],
        {
            "turn_id": second_payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": "snapshot_committed",
            "recorded_at": recorded_at,
            "thread_id": "thr_txn_snapshot_repeat",
            "required_event_ids": _required_turn_event_ids(second_payload["turn_id"], has_outbound=True),
            "has_thread": True,
            "baseline_thread": baseline_thread,
            "target_snapshot": target_snapshot,
            "history_entry": baseline_thread,
        },
    )

    recovery = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    replay = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_snapshot_repeat"])
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_txn_snapshot_repeat"])

    assert recovery["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert thread["title"] == "second"
    assert thread["meta"]["revision"] == 2
    assert len(history) == 1
    assert history[0]["meta"]["revision"] == 1
    assert len(_turn_raw_events(store_root, second_payload["turn_id"])) == 2
    assert not txn_path.exists()


def test_project_turn_recovers_from_history_committed_txn_stage(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "txn-history-committed-store"
    first_payload = {
        "turn_id": "agent:e2e:txn:history:0001",
        "user_text": "第一次记录。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_txn_history", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:e2e:txn:history:0002",
        "user_text": "第二次记录。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_txn_history", "title": "second", "status": "planned"},
    }
    cli_runner.run_json(store_root, "project-turn", payload=first_payload)
    baseline_thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_history"])

    template_store = scratch_root / "txn-history-committed-template"
    cli_runner.run_json(template_store, "project-turn", payload=first_payload)
    cli_runner.run_json(template_store, "project-turn", payload=second_payload)
    template_events = _turn_raw_events(template_store, second_payload["turn_id"])
    fingerprint = template_events[0]["payload"]["_timeline_memory"]["fingerprint"]
    recorded_at = template_events[0]["recorded_at"]
    target_snapshot = cli_runner.run_json(template_store, "get-thread", args=["--thread-id", "thr_txn_history"])

    raw_path = store_root / "raw_events.jsonl"
    with open(raw_path, "a", encoding="utf-8") as handle:
        for record in template_events:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    _thread_snapshot_path(store_root, "thr_txn_history").write_text(
        json.dumps(target_snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    history_path = _thread_history_path(store_root, "thr_txn_history")
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(baseline_thread, ensure_ascii=False) + "\n", encoding="utf-8")

    txn_path = _write_project_turn_txn(
        store_root,
        second_payload["turn_id"],
        {
            "turn_id": second_payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": "history_committed",
            "recorded_at": recorded_at,
            "thread_id": "thr_txn_history",
            "required_event_ids": _required_turn_event_ids(second_payload["turn_id"], has_outbound=True),
            "has_thread": True,
            "baseline_thread": baseline_thread,
            "target_snapshot": target_snapshot,
            "history_entry": baseline_thread,
        },
    )

    result = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_history"])
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_txn_history"])

    assert result["idempotent_replay"] is False
    assert thread["title"] == "second"
    assert thread["meta"]["revision"] == 2
    assert len(history) == 1
    assert history[0]["meta"]["revision"] == 1
    assert not txn_path.exists()


def test_project_turn_repeated_recovery_from_history_committed_txn_keeps_single_history_entry(
    cli_runner, scratch_root: Path
) -> None:
    store_root = scratch_root / "txn-history-repeat-store"
    first_payload = {
        "turn_id": "agent:e2e:txn:history-repeat:0001",
        "user_text": "第一次记录。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_txn_history_repeat", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:e2e:txn:history-repeat:0002",
        "user_text": "第二次记录。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_txn_history_repeat", "title": "second", "status": "planned"},
    }
    cli_runner.run_json(store_root, "project-turn", payload=first_payload)
    baseline_thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_history_repeat"])

    template_store = scratch_root / "txn-history-repeat-template"
    cli_runner.run_json(template_store, "project-turn", payload=first_payload)
    cli_runner.run_json(template_store, "project-turn", payload=second_payload)
    template_events = _turn_raw_events(template_store, second_payload["turn_id"])
    fingerprint = template_events[0]["payload"]["_timeline_memory"]["fingerprint"]
    recorded_at = template_events[0]["recorded_at"]
    target_snapshot = cli_runner.run_json(template_store, "get-thread", args=["--thread-id", "thr_txn_history_repeat"])

    raw_path = store_root / "raw_events.jsonl"
    with open(raw_path, "a", encoding="utf-8") as handle:
        for record in template_events:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    _thread_snapshot_path(store_root, "thr_txn_history_repeat").write_text(
        json.dumps(target_snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    history_path = _thread_history_path(store_root, "thr_txn_history_repeat")
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.write_text(json.dumps(baseline_thread, ensure_ascii=False) + "\n", encoding="utf-8")

    txn_path = _write_project_turn_txn(
        store_root,
        second_payload["turn_id"],
        {
            "turn_id": second_payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": "history_committed",
            "recorded_at": recorded_at,
            "thread_id": "thr_txn_history_repeat",
            "required_event_ids": _required_turn_event_ids(second_payload["turn_id"], has_outbound=True),
            "has_thread": True,
            "baseline_thread": baseline_thread,
            "target_snapshot": target_snapshot,
            "history_entry": baseline_thread,
        },
    )

    recovery = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    replay = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_txn_history_repeat"])
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_txn_history_repeat"])

    assert recovery["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    assert thread["title"] == "second"
    assert thread["meta"]["revision"] == 2
    assert len(history) == 1
    assert history[0]["meta"]["revision"] == 1
    assert len(_turn_raw_events(store_root, second_payload["turn_id"])) == 2
    assert not txn_path.exists()


def test_project_turn_prepared_recovery_then_replay_matches_reference_state(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "txn-prepared-reference-store"
    payload = {
        "turn_id": "agent:e2e:txn:prepared-reference:0001",
        "user_text": "prepared 阶段组合回归。",
        "assistant_text": "继续完成提交。",
        "thread": {"thread_id": "thr_txn_prepared_reference", "title": "prepared-reference", "status": "planned"},
    }
    template_store = scratch_root / "txn-prepared-reference-template"
    cli_runner.run_json(template_store, "project-turn", payload=payload)
    template_events = _turn_raw_events(template_store, payload["turn_id"])
    fingerprint = template_events[0]["payload"]["_timeline_memory"]["fingerprint"]
    recorded_at = template_events[0]["recorded_at"]

    txn_path = _write_project_turn_txn(
        store_root,
        payload["turn_id"],
        {
            "turn_id": payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": "prepared",
            "recorded_at": recorded_at,
            "thread_id": "thr_txn_prepared_reference",
            "required_event_ids": _required_turn_event_ids(payload["turn_id"], has_outbound=True),
            "has_thread": True,
            "baseline_thread": None,
            "target_snapshot": None,
            "history_entry": None,
        },
    )

    recovery = cli_runner.run_json(store_root, "project-turn", payload=payload)
    replay = cli_runner.run_json(store_root, "project-turn", payload=payload)

    assert recovery["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    _assert_turn_state_matches_reference(
        expected_store=template_store,
        actual_store=store_root,
        thread_id="thr_txn_prepared_reference",
        turn_id=payload["turn_id"],
    )
    assert not txn_path.exists()


@pytest.mark.parametrize(
    ("stage", "suffix"),
    [
        ("raw_committed", "raw"),
        ("snapshot_committed", "snapshot"),
        ("history_committed", "history"),
    ],
)
def test_project_turn_stage_recovery_then_replay_matches_reference_state(
    cli_runner, scratch_root: Path, stage: str, suffix: str
) -> None:
    store_root = scratch_root / f"txn-{suffix}-reference-store"
    first_payload = {
        "turn_id": f"agent:e2e:txn:{suffix}-reference:0001",
        "user_text": "第一次记录。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": f"thr_txn_{suffix}_reference", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": f"agent:e2e:txn:{suffix}-reference:0002",
        "user_text": "第二次记录。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": f"thr_txn_{suffix}_reference", "title": "second", "status": "planned"},
    }
    thread_id = f"thr_txn_{suffix}_reference"

    cli_runner.run_json(store_root, "project-turn", payload=first_payload)
    baseline_thread = _read_thread_snapshot(store_root, thread_id)

    template_store = scratch_root / f"txn-{suffix}-reference-template"
    cli_runner.run_json(template_store, "project-turn", payload=first_payload)
    cli_runner.run_json(template_store, "project-turn", payload=second_payload)
    template_events = _turn_raw_events(template_store, second_payload["turn_id"])
    fingerprint = template_events[0]["payload"]["_timeline_memory"]["fingerprint"]
    recorded_at = template_events[0]["recorded_at"]
    target_snapshot = _read_thread_snapshot(template_store, thread_id)
    template_history = _read_thread_history(template_store, thread_id)

    _append_raw_records(store_root, template_events)
    if stage in {"snapshot_committed", "history_committed"}:
        _write_thread_snapshot(store_root, thread_id, target_snapshot)
    if stage == "history_committed":
        _write_thread_history(store_root, thread_id, template_history)

    txn_path = _write_project_turn_txn(
        store_root,
        second_payload["turn_id"],
        {
            "turn_id": second_payload["turn_id"],
            "fingerprint": fingerprint,
            "stage": stage,
            "recorded_at": recorded_at,
            "thread_id": thread_id,
            "required_event_ids": _required_turn_event_ids(second_payload["turn_id"], has_outbound=True),
            "has_thread": True,
            "baseline_thread": baseline_thread,
            "target_snapshot": target_snapshot if stage in {"snapshot_committed", "history_committed"} else None,
            "history_entry": baseline_thread if stage in {"snapshot_committed", "history_committed"} else None,
        },
    )

    recovery = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    replay = cli_runner.run_json(store_root, "project-turn", payload=second_payload)

    assert recovery["idempotent_replay"] is False
    assert replay["idempotent_replay"] is True
    _assert_turn_state_matches_reference(
        expected_store=template_store,
        actual_store=store_root,
        thread_id=thread_id,
        turn_id=second_payload["turn_id"],
    )
    assert not txn_path.exists()


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
    inbound_line = _build_inbound_raw_line(payload)

    inbound_only_store = scratch_root / "repair-inbound"
    inbound_only_store.mkdir(parents=True, exist_ok=True)
    (inbound_only_store / "raw_events.jsonl").write_text(f"{inbound_line}\n", encoding="utf-8")
    inbound_recovery = cli_runner.run_json(inbound_only_store, "project-turn", payload=payload)
    repaired_thread = cli_runner.run_json(inbound_only_store, "get-thread", args=["--thread-id", "thr_repair"])

    assert inbound_recovery["idempotent_replay"] is False
    assert inbound_recovery["recorded_event_ids"] == ["agent:e2e:repair:0001:in", "agent:e2e:repair:0001:out"]
    assert len(_raw_event_lines(inbound_only_store)) == 2
    assert repaired_thread["title"] == "repair"
    _assert_turn_semantics(
        raw_events=_turn_raw_events(inbound_only_store, payload["turn_id"]),
        thread_payload=repaired_thread,
        turn_id=payload["turn_id"],
        has_outbound=True,
    )

    no_thread_payload = {
        "turn_id": "agent:e2e:repair:0001-no-thread",
        "user_text": "只有 raw event，需要补齐 outbound。",
        "assistant_text": "已补齐 outbound。",
    }
    no_thread_inbound_line = _build_inbound_raw_line(no_thread_payload)

    no_thread_store = scratch_root / "repair-no-thread"
    no_thread_store.mkdir(parents=True, exist_ok=True)
    (no_thread_store / "raw_events.jsonl").write_text(f"{no_thread_inbound_line}\n", encoding="utf-8")
    no_thread_replay = cli_runner.run_json(no_thread_store, "project-turn", payload=no_thread_payload)

    assert no_thread_replay["idempotent_replay"] is False
    assert no_thread_replay["recorded_event_ids"] == [
        "agent:e2e:repair:0001-no-thread:in",
        "agent:e2e:repair:0001-no-thread:out",
    ]
    assert no_thread_replay["thread"] is None
    assert len(_raw_event_lines(no_thread_store)) == 2
    _assert_turn_semantics(
        raw_events=_turn_raw_events(no_thread_store, no_thread_payload["turn_id"]),
        thread_payload=None,
        turn_id=no_thread_payload["turn_id"],
        has_outbound=True,
    )

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


def test_existing_thread_inbound_only_replay_recovers_next_revision(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "repair-existing-thread"
    first_payload = {
        "turn_id": "agent:e2e:repair-existing:0001",
        "user_text": "第一次记录线程。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_existing_repair", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:e2e:repair-existing:0002",
        "user_text": "第二次更新线程。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_existing_repair", "title": "second", "status": "planned"},
    }

    cli_runner.run_json(store_root, "project-turn", payload=first_payload)
    inbound_line = _build_inbound_raw_line(second_payload)
    with open(store_root / "raw_events.jsonl", "a", encoding="utf-8") as handle:
        handle.write(f"{inbound_line}\n")

    replay = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_existing_repair"])
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_existing_repair"])

    assert replay["idempotent_replay"] is False
    assert replay["recorded_event_ids"] == [
        "agent:e2e:repair-existing:0002:in",
        "agent:e2e:repair-existing:0002:out",
    ]
    assert thread["meta"]["revision"] == 2
    assert thread["title"] == "second"
    _assert_turn_semantics(
        raw_events=_turn_raw_events(store_root, second_payload["turn_id"]),
        thread_payload=thread,
        turn_id=second_payload["turn_id"],
        has_outbound=True,
    )
    assert _event_ref_ids(thread) == [
        "agent:e2e:repair-existing:0001:in",
        "agent:e2e:repair-existing:0001:out",
        "agent:e2e:repair-existing:0002:in",
        "agent:e2e:repair-existing:0002:out",
    ]
    assert len(history) == 1
    assert history[0]["meta"]["revision"] == 1
    assert _event_ref_ids(history[0]) == [
        "agent:e2e:repair-existing:0001:in",
        "agent:e2e:repair-existing:0001:out",
    ]
    assert len(_raw_event_lines(store_root)) == 4


def test_existing_thread_replay_deduplicates_history_after_append_only_crash(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "repair-existing-history-append"
    first_payload = {
        "turn_id": "agent:e2e:repair-history:0001",
        "user_text": "第一次记录线程。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_history_repair", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:e2e:repair-history:0002",
        "user_text": "第二次更新线程。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_history_repair", "title": "second", "status": "planned"},
    }

    cli_runner.run_json(store_root, "project-turn", payload=first_payload)
    template_store = scratch_root / "repair-history-template"
    cli_runner.run_json(template_store, "project-turn", payload=second_payload)

    raw_path = store_root / "raw_events.jsonl"
    for line in _raw_event_lines(template_store):
        with open(raw_path, "a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")

    snapshot_before = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_history_repair"])
    history_path = _thread_history_path(store_root, "thr_history_repair")
    with open(history_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(snapshot_before, ensure_ascii=False) + "\n")

    replay = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_history_repair"])
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_history_repair"])

    assert replay["idempotent_replay"] is False
    assert thread["meta"]["revision"] == 2
    assert thread["title"] == "second"
    assert len(history) == 1
    assert history[0]["meta"]["revision"] == 1
    assert _event_ref_ids(history[0]) == [
        "agent:e2e:repair-history:0001:in",
        "agent:e2e:repair-history:0001:out",
    ]


def test_existing_thread_replay_rejects_snapshot_without_previous_revision_in_history(
    cli_runner, scratch_root: Path
) -> None:
    store_root = scratch_root / "repair-existing-history-gap"
    template_store = scratch_root / "repair-existing-history-gap-template"
    first_payload = {
        "turn_id": "agent:e2e:repair-gap:0001",
        "user_text": "第一次记录线程。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_history_gap", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:e2e:repair-gap:0002",
        "user_text": "第二次更新线程。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_history_gap", "title": "second", "status": "planned"},
    }

    cli_runner.run_json(store_root, "project-turn", payload=first_payload)
    cli_runner.run_json(template_store, "project-turn", payload=first_payload)
    cli_runner.run_json(template_store, "project-turn", payload=second_payload)

    raw_path = store_root / "raw_events.jsonl"
    for line in _raw_event_lines(template_store):
        if second_payload["turn_id"] not in line:
            continue
        with open(raw_path, "a", encoding="utf-8") as handle:
            handle.write(f"{line}\n")

    corrupted_snapshot = _read_thread_snapshot(template_store, "thr_history_gap")
    _write_thread_snapshot(store_root, "thr_history_gap", corrupted_snapshot)

    error = cli_runner.expect_failure_json(store_root, "project-turn", payload=second_payload)
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_history_gap"])

    assert error["error"]["code"] == "TM_PARTIAL_WRITE"
    assert "previous revision in history" in error["error"]["message"]
    assert history == []


def test_missing_snapshot_replay_preserves_multiturn_thread_state(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "repair-multiturn-snapshot"
    first_payload = {
        "turn_id": "agent:e2e:repair-snapshot:0001",
        "user_text": "第一次记录线程。",
        "assistant_text": "已记录第一次。",
        "thread": {"thread_id": "thr_snapshot_repair", "title": "first", "status": "planned"},
    }
    second_payload = {
        "turn_id": "agent:e2e:repair-snapshot:0002",
        "user_text": "第二次更新线程。",
        "assistant_text": "已记录第二次。",
        "thread": {"thread_id": "thr_snapshot_repair", "title": "second", "status": "planned"},
    }

    cli_runner.run_json(store_root, "project-turn", payload=first_payload)
    cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    thread_before = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_snapshot_repair"])
    history_before = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_snapshot_repair"])

    for path in (store_root / "threads").glob("*.json"):
        path.unlink()

    replay = cli_runner.run_json(store_root, "project-turn", payload=second_payload)
    thread_after = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_snapshot_repair"])
    history_after = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_snapshot_repair"])

    assert replay["idempotent_replay"] is False
    assert thread_after["meta"]["revision"] == thread_before["meta"]["revision"]
    assert thread_after["created_at"] == thread_before["created_at"]
    assert thread_after["first_event_at"] == thread_before["first_event_at"]
    assert _event_ref_ids(thread_after) == _event_ref_ids(thread_before)
    assert history_after == history_before
    assert len(_raw_event_lines(store_root)) == 4


def test_list_threads_orders_by_absolute_time_across_offsets(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "mixed-offset-order-store"
    _write_thread_snapshot(
        store_root,
        "thr_late_utc",
        _thread_snapshot_payload(
            thread_id="thr_late_utc",
            title="late-utc",
            last_event_at="2026-03-24T09:00:00+00:00",
            updated_at="2026-03-24T09:00:00+00:00",
        ),
    )
    _write_thread_snapshot(
        store_root,
        "thr_early_hk",
        _thread_snapshot_payload(
            thread_id="thr_early_hk",
            title="early-hk",
            last_event_at="2026-03-24T10:00:00+08:00",
            updated_at="2026-03-24T10:00:00+08:00",
        ),
    )

    threads = cli_runner.run_json(store_root, "list-threads")

    assert [thread["thread_id"] for thread in threads] == ["thr_late_utc", "thr_early_hk"]


def test_list_threads_pagination_uses_wrapper_only_in_explicit_paging_mode(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "list-threads-pagination-store"
    _write_thread_snapshot(
        store_root,
        "thr_page_3",
        _thread_snapshot_payload(
            thread_id="thr_page_3",
            title="page-3",
            last_event_at="2026-03-24T11:00:00+00:00",
            updated_at="2026-03-24T11:00:00+00:00",
        ),
    )
    _write_thread_snapshot(
        store_root,
        "thr_page_2",
        _thread_snapshot_payload(
            thread_id="thr_page_2",
            title="page-2",
            last_event_at="2026-03-24T10:00:00+00:00",
            updated_at="2026-03-24T10:00:00+00:00",
        ),
    )
    _write_thread_snapshot(
        store_root,
        "thr_page_1",
        _thread_snapshot_payload(
            thread_id="thr_page_1",
            title="page-1",
            last_event_at="2026-03-24T09:00:00+00:00",
            updated_at="2026-03-24T09:00:00+00:00",
        ),
    )

    full_list = cli_runner.run_json(store_root, "list-threads")
    first_page = cli_runner.run_json(store_root, "list-threads", args=["--limit", "2"])

    assert [thread["thread_id"] for thread in full_list] == ["thr_page_3", "thr_page_2", "thr_page_1"]
    assert [thread["thread_id"] for thread in first_page["items"]] == ["thr_page_3", "thr_page_2"]
    assert first_page["has_more"] is True
    assert isinstance(first_page["next_cursor"], str) and first_page["next_cursor"]

    second_page = cli_runner.run_json(
        store_root,
        "list-threads",
        args=["--limit", "2", "--cursor", first_page["next_cursor"]],
    )

    assert [thread["thread_id"] for thread in second_page["items"]] == ["thr_page_1"]
    assert second_page["has_more"] is False
    assert second_page["next_cursor"] is None


def test_list_threads_filters_last_event_window_and_paginates_filtered_result(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "list-threads-time-window-store"
    _write_thread_snapshot(
        store_root,
        "thr_window_after",
        _thread_snapshot_payload(
            thread_id="thr_window_after",
            title="window-after",
            last_event_at="2026-03-24T11:00:00+00:00",
            updated_at="2026-03-24T11:00:00+00:00",
        ),
    )
    _write_thread_snapshot(
        store_root,
        "thr_window_mid",
        _thread_snapshot_payload(
            thread_id="thr_window_mid",
            title="window-mid",
            last_event_at="2026-03-24T10:00:00+00:00",
            updated_at="2026-03-24T10:00:00+00:00",
        ),
    )
    _write_thread_snapshot(
        store_root,
        "thr_window_start",
        _thread_snapshot_payload(
            thread_id="thr_window_start",
            title="window-start",
            last_event_at="2026-03-24T09:00:00+00:00",
            updated_at="2026-03-24T09:00:00+00:00",
        ),
    )
    _write_thread_snapshot(
        store_root,
        "thr_window_missing",
        _thread_snapshot_payload(
            thread_id="thr_window_missing",
            title="window-missing",
            last_event_at=None,
            updated_at="2026-03-24T12:00:00+00:00",
        ),
    )

    filtered = cli_runner.run_json(
        store_root,
        "list-threads",
        args=[
            "--last-event-at-or-after",
            "2026-03-24T09:00:00+00:00",
            "--last-event-at-or-before",
            "2026-03-24T10:00:00+00:00",
        ],
    )
    first_page = cli_runner.run_json(
        store_root,
        "list-threads",
        args=[
            "--last-event-at-or-after",
            "2026-03-24T09:00:00+00:00",
            "--last-event-at-or-before",
            "2026-03-24T10:00:00+00:00",
            "--limit",
            "1",
        ],
    )

    assert [thread["thread_id"] for thread in filtered] == ["thr_window_mid", "thr_window_start"]
    assert [thread["thread_id"] for thread in first_page["items"]] == ["thr_window_mid"]
    assert first_page["has_more"] is True

    second_page = cli_runner.run_json(
        store_root,
        "list-threads",
        args=[
            "--last-event-at-or-after",
            "2026-03-24T09:00:00+00:00",
            "--last-event-at-or-before",
            "2026-03-24T10:00:00+00:00",
            "--limit",
            "1",
            "--cursor",
            first_page["next_cursor"],
        ],
    )

    assert [thread["thread_id"] for thread in second_page["items"]] == ["thr_window_start"]
    assert second_page["has_more"] is False


def test_list_threads_invalid_paging_arguments_return_invalid_argument(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "list-threads-invalid-args-store"
    _write_thread_snapshot(
        store_root,
        "thr_invalid_cursor_2",
        _thread_snapshot_payload(
            thread_id="thr_invalid_cursor_2",
            title="invalid-cursor-2",
            status="planned",
            last_event_at="2026-03-24T10:00:00+00:00",
            updated_at="2026-03-24T10:00:00+00:00",
        ),
    )
    _write_thread_snapshot(
        store_root,
        "thr_invalid_cursor_1",
        _thread_snapshot_payload(
            thread_id="thr_invalid_cursor_1",
            title="invalid-cursor-1",
            status="planned",
            last_event_at="2026-03-24T09:00:00+00:00",
            updated_at="2026-03-24T09:00:00+00:00",
        ),
    )
    first_page = cli_runner.run_json(store_root, "list-threads", args=["--status", "planned", "--limit", "1"])

    invalid_limit = cli_runner.expect_failure_json(store_root, "list-threads", args=["--limit", "0"])
    invalid_cursor = cli_runner.expect_failure_json(store_root, "list-threads", args=["--cursor", "not-a-cursor"])
    invalid_window = cli_runner.expect_failure_json(
        store_root,
        "list-threads",
        args=[
            "--last-event-at-or-after",
            "2026-03-24T10:00:00+00:00",
            "--last-event-at-or-before",
            "2026-03-24T09:00:00+00:00",
        ],
    )
    mismatched_cursor = cli_runner.expect_failure_json(
        store_root,
        "list-threads",
        args=["--status", "done", "--limit", "1", "--cursor", first_page["next_cursor"]],
    )
    cursor_padding = "=" * (-len(first_page["next_cursor"]) % 4)
    decoded_cursor = json.loads(
        base64.urlsafe_b64decode(f"{first_page['next_cursor']}{cursor_padding}").decode("utf-8")
    )
    decoded_cursor["position"]["updated_at"] = "not-a-timestamp"
    tampered_updated_at_cursor = base64.urlsafe_b64encode(
        json.dumps(decoded_cursor, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    invalid_cursor_timestamp = cli_runner.expect_failure_json(
        store_root,
        "list-threads",
        args=["--status", "planned", "--limit", "1", "--cursor", tampered_updated_at_cursor],
    )
    decoded_cursor["position"]["updated_at"] = "2026-03-24T10:00:00+00:00"
    decoded_cursor["position"]["last_event_at"] = "not-a-timestamp"
    tampered_last_event_at_cursor = base64.urlsafe_b64encode(
        json.dumps(decoded_cursor, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")
    invalid_last_event_at_cursor = cli_runner.expect_failure_json(
        store_root,
        "list-threads",
        args=["--status", "planned", "--limit", "1", "--cursor", tampered_last_event_at_cursor],
    )

    assert invalid_limit["error"]["code"] == "TM_INVALID_ARGUMENT"
    assert "limit must be a positive integer" in invalid_limit["error"]["message"]
    assert invalid_cursor["error"]["code"] == "TM_INVALID_ARGUMENT"
    assert "cursor is invalid" in invalid_cursor["error"]["message"]
    assert invalid_window["error"]["code"] == "TM_INVALID_ARGUMENT"
    assert "last_event_at_or_after must be <=" in invalid_window["error"]["message"]
    assert mismatched_cursor["error"]["code"] == "TM_INVALID_ARGUMENT"
    assert "cursor does not match current filters" in mismatched_cursor["error"]["message"]
    assert invalid_cursor_timestamp["error"]["code"] == "TM_INVALID_ARGUMENT"
    assert "cursor is invalid" in invalid_cursor_timestamp["error"]["message"]
    assert invalid_last_event_at_cursor["error"]["code"] == "TM_INVALID_ARGUMENT"
    assert "cursor is invalid" in invalid_last_event_at_cursor["error"]["message"]


def test_real_cli_invalid_cursor_keeps_stderr_as_single_json(
    cli_path: Path,
    repo_root: Path,
    cli_runner,
    scratch_root: Path,
) -> None:
    store_root = scratch_root / "list-threads-real-cli-invalid-cursor-store"
    _write_thread_snapshot(
        store_root,
        "thr_real_cli_invalid_cursor_2",
        _thread_snapshot_payload(
            thread_id="thr_real_cli_invalid_cursor_2",
            title="real-cli-invalid-cursor-2",
            status="planned",
            last_event_at="2026-03-24T10:00:00+00:00",
            updated_at="2026-03-24T10:00:00+00:00",
        ),
    )
    _write_thread_snapshot(
        store_root,
        "thr_real_cli_invalid_cursor_1",
        _thread_snapshot_payload(
            thread_id="thr_real_cli_invalid_cursor_1",
            title="real-cli-invalid-cursor-1",
            status="planned",
            last_event_at="2026-03-24T09:00:00+00:00",
            updated_at="2026-03-24T09:00:00+00:00",
        ),
    )
    first_page = cli_runner.run_json(store_root, "list-threads", args=["--status", "planned", "--limit", "1"])
    cursor_padding = "=" * (-len(first_page["next_cursor"]) % 4)
    decoded_cursor = json.loads(
        base64.urlsafe_b64decode(f"{first_page['next_cursor']}{cursor_padding}").decode("utf-8")
    )
    decoded_cursor["position"]["updated_at"] = "not-a-timestamp"
    tampered_cursor = base64.urlsafe_b64encode(
        json.dumps(decoded_cursor, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).decode("ascii").rstrip("=")

    result = subprocess.run(
        [
            *_resolve_python_command(),
            str(cli_path),
            "list-threads",
            "--store-root",
            str(store_root),
            "--status",
            "planned",
            "--limit",
            "1",
            "--cursor",
            tampered_cursor,
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=_real_cli_env(scratch_root),
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout.strip() == ""
    error = json.loads(result.stderr)
    assert error["error"]["code"] == "TM_INVALID_ARGUMENT"
    assert error["error"]["message"] == "list-threads cursor is invalid"


def test_project_turn_rejects_context_recorded_at(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "reject-recorded-at-store"
    error = cli_runner.expect_failure_json(
        store_root,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:reject-recorded-at:0001",
            "user_text": "不应接受 recorded_at。",
            "thread": {"thread_id": "thr_reject_recorded_at", "title": "reject", "status": "planned"},
            "context": {"recorded_at": "2026-03-24T10:00:00+08:00"},
        },
    )

    assert error["error"]["code"] == "TM_INVALID_ARGUMENT"
    assert "context contains unsupported fields: recorded_at" in error["error"]["message"]


@pytest.mark.parametrize(
    ("suffix", "payload_updates", "expected_message"),
    [
        ("context-source-null", {"context": {"source": None}}, "context.source is required"),
        ("context-actor-id-object", {"context": {"actor_id": {}}}, "context.actor_id must be a string"),
        (
            "plan-time-all-day-string",
            {"thread": {"plan_time": {"all_day": "false"}}},
            "thread.plan_time.all_day must be a boolean",
        ),
        ("plan-time-due-at-bool", {"thread": {"plan_time": {"due_at": False}}}, "thread.plan_time.due_at must be a string"),
        ("thread-kind-null", {"thread": {"thread_kind": None}}, "thread.thread_kind is required"),
        ("thread-title-int", {"thread": {"title": 123}}, "thread.title must be a string"),
        ("content-notes-bool", {"thread": {"content": {"notes": False}}}, "thread.content.notes must be a string"),
        ("user-text-int", {"user_text": 123}, "user_text must be a string"),
        ("assistant-text-bool", {"assistant_text": False}, "assistant_text must be a string"),
    ],
)
def test_project_turn_rejects_invalid_contract_types(
    cli_runner, scratch_root: Path, suffix: str, payload_updates: dict, expected_message: str
) -> None:
    store_root = scratch_root / f"reject-contract-types-{suffix}"
    base_thread = {
        "thread_id": f"thr_reject_contract_types_{suffix}",
        "title": "reject",
        "status": "planned",
    }
    payload = {
        "turn_id": f"agent:e2e:reject-contract-types:{suffix}",
        "user_text": "类型必须在边界被验证。",
        "assistant_text": "这是合法回复。",
        "thread": dict(base_thread),
    }
    payload.update(payload_updates)
    if "thread" in payload_updates:
        payload["thread"] = {
            **base_thread,
            **payload_updates["thread"],
        }

    error = cli_runner.expect_failure_json(store_root, "project-turn", payload=payload)

    assert error["error"]["code"] == "TM_INVALID_ARGUMENT"
    assert expected_message in error["error"]["message"]


def test_project_turn_missing_input_file_returns_invalid_argument(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "missing-input-store"
    missing_input = scratch_root / "missing-input.json"

    error = cli_runner.expect_failure_json(
        store_root,
        "project-turn",
        args=["--input", str(missing_input)],
    )

    assert error["error"]["code"] == "TM_INVALID_ARGUMENT"
    assert error["error"]["details"]["path"] == str(missing_input)
    assert "failed to read input JSON" in error["error"]["message"]


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
    error = cli_runner.expect_failure_json(
        store_root,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:conflict:0001",
            "user_text": "新内容",
            "thread": {"thread_id": "thr_conflict", "title": "conflict", "status": "planned"},
        },
    )

    assert error["error"]["code"] == "TM_TURN_CONFLICT"
    assert "different payload already recorded" in error["error"]["message"]


def test_replay_rejects_outbound_only_partial_write(cli_runner, scratch_root: Path) -> None:
    payload = {
        "turn_id": "agent:e2e:missing-inbound:0001",
        "user_text": "先写入完整，再手工删 inbound。",
        "assistant_text": "这是回复。",
        "thread": {"thread_id": "thr_missing_inbound", "title": "missing-inbound", "status": "planned"},
    }
    template_store = scratch_root / "missing-inbound-template"
    cli_runner.run_json(template_store, "project-turn", payload=payload)
    outbound_line = _raw_event_lines(template_store)[1]

    broken_store = scratch_root / "missing-inbound-store"
    broken_store.mkdir(parents=True, exist_ok=True)
    (broken_store / "raw_events.jsonl").write_text(f"{outbound_line}\n", encoding="utf-8")

    error = cli_runner.expect_failure_json(broken_store, "project-turn", payload=payload)

    assert error["error"]["code"] == "TM_PARTIAL_WRITE"
    assert "partial write detected" in error["error"]["message"]
    assert "missing inbound" in error["error"]["message"]


def test_replay_rejects_raw_event_without_timeline_metadata(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "missing-meta-store"
    store_root.mkdir(parents=True, exist_ok=True)
    inbound = {
        "event_id": "agent:e2e:missing-meta:0001:in",
        "event_type": "user_message",
        "recorded_at": "2026-03-24T10:00:00+08:00",
        "source": "skill://timeline-memory",
        "actor_kind": "user",
        "actor_id": "user",
        "raw_text": "缺少 timeline 元数据",
        "payload": {"message": "缺少 timeline 元数据"},
        "schema_version": 1,
    }
    (store_root / "raw_events.jsonl").write_text(json.dumps(inbound, ensure_ascii=False) + "\n", encoding="utf-8")

    error = cli_runner.expect_failure_json(
        store_root,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:missing-meta:0001",
            "user_text": "缺少 timeline 元数据",
        },
    )

    assert error["error"]["code"] == "TM_METADATA_CONFLICT"
    assert "missing _timeline_memory metadata" in error["error"]["message"]


def test_project_turn_default_read_mode_remains_compat_with_malformed_jsonl(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "compat-read-mode-store"
    payload = {
        "turn_id": "agent:e2e:compat-read-mode:0001",
        "user_text": "默认读取模式应保持兼容。",
        "assistant_text": "已按兼容模式继续写入。",
        "thread": {"thread_id": "thr_compat_default", "title": "compat-default", "status": "planned"},
    }
    store_root.mkdir(parents=True, exist_ok=True)
    (store_root / "raw_events.jsonl").write_text("{bad json}\n", encoding="utf-8")

    result = cli_runner.run_json(store_root, "project-turn", payload=payload)

    assert result["ok"] is True
    assert result["idempotent_replay"] is False
    assert result["recorded_event_ids"] == [
        "agent:e2e:compat-read-mode:0001:in",
        "agent:e2e:compat-read-mode:0001:out",
    ]
    assert len(_raw_event_lines(store_root)) == 3
    assert _raw_event_lines(store_root)[0] == "{bad json}"


def test_project_turn_strict_read_mode_fails_on_malformed_jsonl(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "strict-read-mode-store"
    payload = {
        "turn_id": "agent:e2e:strict-read-mode:0001",
        "user_text": "严格模式应在坏行时失败。",
        "assistant_text": "不应继续写入。",
        "thread": {"thread_id": "thr_strict_fail", "title": "strict-fail", "status": "planned"},
    }
    store_root.mkdir(parents=True, exist_ok=True)
    raw_path = store_root / "raw_events.jsonl"
    raw_path.write_text("{bad json}\n", encoding="utf-8")

    error = cli_runner.expect_failure_json(
        store_root,
        "project-turn",
        payload=payload,
        args=["--read-mode", "strict"],
    )

    assert error["error"]["code"] == "TM_READ_FAILED"
    assert "failed to read JSONL" in error["error"]["message"]
    assert str(raw_path) in error["error"]["message"]
    assert error["error"]["details"]["path"] == str(raw_path)
    assert error["error"]["details"]["line_no"] == 1
    assert "malformed JSON" in error["error"]["message"]
    assert _raw_event_lines(store_root) == ["{bad json}"]


def test_list_thread_history_strict_read_mode_fails_on_non_object_jsonl(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "strict-history-read-mode-store"
    cli_runner.run_json(
        store_root,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:strict-history-read-mode:0001",
            "user_text": "先创建线程历史。",
            "thread": {"thread_id": "thr_strict_history", "title": "strict-history", "status": "planned"},
        },
    )
    cli_runner.run_json(
        store_root,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:strict-history-read-mode:0002",
            "user_text": "再更新一次，生成 history。",
            "thread": {"thread_id": "thr_strict_history", "title": "strict-history-2", "status": "done"},
        },
    )
    history_path = _thread_history_path(store_root, "thr_strict_history")
    history_path.write_text("[]\n" + history_path.read_text(encoding="utf-8"), encoding="utf-8")

    compat_history = cli_runner.run_json(
        store_root,
        "list-thread-history",
        args=["--thread-id", "thr_strict_history", "--read-mode", "compat"],
    )
    error = cli_runner.expect_failure_json(
        store_root,
        "list-thread-history",
        args=["--thread-id", "thr_strict_history", "--read-mode", "strict"],
    )

    assert len(compat_history) == 1
    assert compat_history[0]["title"] == "strict-history"
    assert error["error"]["code"] == "TM_READ_FAILED"
    assert "failed to read JSONL" in error["error"]["message"]
    assert str(history_path) in error["error"]["message"]
    assert error["error"]["details"]["path"] == str(history_path)
    assert error["error"]["details"]["line_no"] == 1
    assert "expected JSON object" in error["error"]["message"]


def test_replay_rejects_inconsistent_thread_metadata(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "inconsistent-thread-meta-store"
    payload = {
        "turn_id": "agent:e2e:thread-meta-mismatch:0001",
        "user_text": "创建线程",
        "assistant_text": "已创建",
        "thread": {"thread_id": "thr_expected", "title": "expected", "status": "planned"},
    }
    cli_runner.run_json(store_root, "project-turn", payload=payload)

    raw_path = store_root / "raw_events.jsonl"
    records = [json.loads(line) for line in raw_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    records[0]["payload"]["_timeline_memory"]["thread_id"] = "thr_other"
    raw_path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")

    error = cli_runner.expect_failure_json(store_root, "project-turn", payload=payload)

    assert error["error"]["code"] == "TM_METADATA_CONFLICT"
    assert "inconsistent thread metadata" in error["error"]["message"]


def test_replay_rejects_partially_reflected_thread_snapshot(cli_runner, scratch_root: Path) -> None:
    store_root = scratch_root / "partial-thread-snapshot-store"
    payload = {
        "turn_id": "agent:e2e:partial-thread-snapshot:0001",
        "user_text": "创建完整 turn",
        "assistant_text": "创建完成",
        "thread": {"thread_id": "thr_partial_snapshot", "title": "partial", "status": "planned"},
    }
    cli_runner.run_json(store_root, "project-turn", payload=payload)

    snapshot_path = _thread_snapshot_path(store_root, "thr_partial_snapshot")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["event_refs"] = [snapshot["event_refs"][0]]
    snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")

    error = cli_runner.expect_failure_json(store_root, "project-turn", payload=payload)

    assert error["error"]["code"] == "TM_PARTIAL_WRITE"
    assert "thread snapshot partially reflects current turn" in error["error"]["message"]


def test_project_turn_real_subprocess_serializes_same_thread_writes(
    cli_path: Path,
    repo_root: Path,
    cli_runner,
    scratch_root: Path,
) -> None:
    store_root = scratch_root / "concurrent-same-thread-store"
    cli_runner.run_json(
        store_root,
        "project-turn",
        payload={
            "turn_id": "agent:e2e:concurrent-thread:0001",
            "user_text": "先创建线程。",
            "assistant_text": "已创建。",
            "thread": {"thread_id": "thr_concurrent", "title": "initial", "status": "planned"},
        },
    )
    first_update = {
        "turn_id": "agent:e2e:concurrent-thread:0002",
        "user_text": "第一次并发更新。",
        "assistant_text": "已更新为 first。",
        "thread": {"thread_id": "thr_concurrent", "title": "first", "status": "planned"},
    }
    second_update = {
        "turn_id": "agent:e2e:concurrent-thread:0003",
        "user_text": "第二次并发更新。",
        "assistant_text": "已更新为 second。",
        "thread": {"thread_id": "thr_concurrent", "title": "second", "status": "done"},
    }

    first_process = _start_real_project_turn(
        repo_root,
        cli_path,
        scratch_root,
        store_root,
        payload=first_update,
        input_name="concurrent-thread-first.json",
        extra_env={"TIMELINE_TEST_PROJECT_TURN_LOCK_HOLD_SECONDS": "0.4"},
    )
    try:
        _wait_for_lock_owner(store_root, first_update["turn_id"])
        second_process = _start_real_project_turn(
            repo_root,
            cli_path,
            scratch_root,
            store_root,
            payload=second_update,
            input_name="concurrent-thread-second.json",
        )
        second_stdout, second_stderr = second_process.communicate(timeout=10)
    finally:
        first_stdout, first_stderr = first_process.communicate(timeout=10)

    assert first_process.returncode == 0, first_stderr.strip()
    assert second_process.returncode == 0, second_stderr.strip()

    first_result = json.loads(first_stdout)
    second_result = json.loads(second_stdout)
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_concurrent"])
    history = cli_runner.run_json(store_root, "list-thread-history", args=["--thread-id", "thr_concurrent"])

    assert first_result["thread"]["meta"]["revision"] == 2
    assert second_result["thread"]["meta"]["revision"] == 3
    assert thread["title"] == "second"
    assert thread["status"] == "done"
    assert thread["meta"]["revision"] == 3
    assert [entry["meta"]["revision"] for entry in history] == [1, 2]
    assert [entry["title"] for entry in history] == ["initial", "first"]


def test_project_turn_real_subprocess_same_turn_id_stays_idempotent_under_lock_contention(
    cli_path: Path,
    repo_root: Path,
    cli_runner,
    scratch_root: Path,
) -> None:
    store_root = scratch_root / "concurrent-same-turn-store"
    payload = {
        "turn_id": "agent:e2e:concurrent-idempotent:0001",
        "user_text": "并发提交相同 turn。",
        "assistant_text": "应该只写一次。",
        "thread": {"thread_id": "thr_concurrent_idempotent", "title": "same-turn", "status": "planned"},
    }

    first_process = _start_real_project_turn(
        repo_root,
        cli_path,
        scratch_root,
        store_root,
        payload=payload,
        input_name="concurrent-idempotent-first.json",
        extra_env={"TIMELINE_TEST_PROJECT_TURN_LOCK_HOLD_SECONDS": "0.4"},
    )
    try:
        _wait_for_lock_owner(store_root, payload["turn_id"])
        second_process = _start_real_project_turn(
            repo_root,
            cli_path,
            scratch_root,
            store_root,
            payload=payload,
            input_name="concurrent-idempotent-second.json",
        )
        second_stdout, second_stderr = second_process.communicate(timeout=10)
    finally:
        first_stdout, first_stderr = first_process.communicate(timeout=10)

    assert first_process.returncode == 0, first_stderr.strip()
    assert second_process.returncode == 0, second_stderr.strip()

    results = [json.loads(first_stdout), json.loads(second_stdout)]
    thread = cli_runner.run_json(store_root, "get-thread", args=["--thread-id", "thr_concurrent_idempotent"])

    assert sorted(result["idempotent_replay"] for result in results) == [False, True]
    assert len(_turn_raw_events(store_root, payload["turn_id"])) == 2
    assert thread["meta"]["revision"] == 1
    assert not _project_turn_txn_path(store_root, payload["turn_id"]).exists()


def test_project_turn_real_subprocess_same_turn_id_conflict_remains_predictable(
    cli_path: Path,
    repo_root: Path,
    scratch_root: Path,
) -> None:
    store_root = scratch_root / "concurrent-conflict-store"
    first_payload = {
        "turn_id": "agent:e2e:concurrent-conflict:0001",
        "user_text": "先提交一版。",
        "assistant_text": "第一版。",
        "thread": {"thread_id": "thr_concurrent_conflict", "title": "first", "status": "planned"},
    }
    conflicting_payload = {
        "turn_id": "agent:e2e:concurrent-conflict:0001",
        "user_text": "先提交一版。",
        "assistant_text": "冲突版本。",
        "thread": {"thread_id": "thr_concurrent_conflict", "title": "conflict", "status": "planned"},
    }

    first_process = _start_real_project_turn(
        repo_root,
        cli_path,
        scratch_root,
        store_root,
        payload=first_payload,
        input_name="concurrent-conflict-first.json",
        extra_env={"TIMELINE_TEST_PROJECT_TURN_LOCK_HOLD_SECONDS": "0.4"},
    )
    try:
        _wait_for_lock_owner(store_root, first_payload["turn_id"])
        second_process = _start_real_project_turn(
            repo_root,
            cli_path,
            scratch_root,
            store_root,
            payload=conflicting_payload,
            input_name="concurrent-conflict-second.json",
        )
        second_stdout, second_stderr = second_process.communicate(timeout=10)
    finally:
        first_stdout, first_stderr = first_process.communicate(timeout=10)

    assert first_process.returncode == 0, first_stderr.strip()
    assert second_process.returncode == 1
    assert second_stdout.strip() == ""
    second_error = json.loads(second_stderr)
    assert second_error["error"]["code"] == "TM_TURN_CONFLICT"
    assert "different payload already recorded" in second_error["error"]["message"]
    assert json.loads(first_stdout)["idempotent_replay"] is False
