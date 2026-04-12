from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


THREAD_EVENT_REF_ROLES = {"primary", "context", "evidence", "derived"}


def require_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return dict(value)


def ensure_allowed_keys(data: dict[str, Any], allowed: set[str], name: str) -> dict[str, Any]:
    extra = sorted(set(data) - allowed)
    if extra:
        raise ValueError(f"{name} contains unsupported fields: {', '.join(extra)}")
    return data


def require_string(value: Any, name: str) -> str:
    if value is None:
        raise ValueError(f"{name} is required")
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def require_optional_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def require_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean")
    return value


def ensure_no_standardized_time_fields(payload: Any) -> None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in {"plan_time", "fact_time"}:
                raise ValueError("raw event payload must not contain plan_time or fact_time")
            ensure_no_standardized_time_fields(value)
    elif isinstance(payload, list):
        for item in payload:
            ensure_no_standardized_time_fields(item)


def normalize_structured_list(values: Any, *, field_name: str) -> list[dict[str, Any]]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError(f"{field_name} must be a list")
    normalized: list[dict[str, Any]] = []
    for item in values:
        if isinstance(item, dict):
            normalized.append(dict(item))
            continue
        normalized.append({"text": str(item)})
    return normalized


@dataclass
class RawEventRecord:
    event_id: str
    event_type: str
    recorded_at: str
    source: str
    actor_kind: str
    actor_id: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    raw_text: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id is required")
        if not self.event_type:
            raise ValueError("event_type is required")
        if not self.recorded_at:
            raise ValueError("recorded_at is required")
        if not self.source:
            raise ValueError("source is required")
        if not self.actor_kind:
            raise ValueError("actor_kind is required")
        self.payload = require_mapping(self.payload, "payload")
        ensure_no_standardized_time_fields(self.payload)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RawEventRecord":
        payload = require_mapping(data, "raw event")
        return cls(
            event_id=str(payload["event_id"]),
            event_type=str(payload["event_type"]),
            recorded_at=str(payload["recorded_at"]),
            source=str(payload["source"]),
            actor_kind=str(payload["actor_kind"]),
            actor_id=str(payload["actor_id"]) if payload.get("actor_id") is not None else None,
            correlation_id=str(payload["correlation_id"]) if payload.get("correlation_id") is not None else None,
            causation_id=str(payload["causation_id"]) if payload.get("causation_id") is not None else None,
            raw_text=str(payload["raw_text"]) if payload.get("raw_text") is not None else None,
            payload=require_mapping(payload.get("payload", {}), "payload"),
            confidence=float(payload["confidence"]) if payload.get("confidence") is not None else None,
            schema_version=int(payload.get("schema_version", 1)),
        )


@dataclass
class ThreadPlanTime:
    planned_start: str | None = None
    planned_end: str | None = None
    due_at: str | None = None
    all_day: bool = False
    rrule: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ThreadPlanTime":
        data = require_mapping(data or {}, "plan_time")
        return cls(
            planned_start=str(data["planned_start"]) if data.get("planned_start") is not None else None,
            planned_end=str(data["planned_end"]) if data.get("planned_end") is not None else None,
            due_at=str(data["due_at"]) if data.get("due_at") is not None else None,
            all_day=bool(data.get("all_day", False)),
            rrule=str(data["rrule"]) if data.get("rrule") is not None else None,
        )


@dataclass
class ThreadFactTime:
    occurred_at: str | None = None
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ThreadFactTime":
        data = require_mapping(data or {}, "fact_time")
        return cls(
            occurred_at=str(data["occurred_at"]) if data.get("occurred_at") is not None else None,
            completed_at=str(data["completed_at"]) if data.get("completed_at") is not None else None,
        )


@dataclass
class ThreadContent:
    notes: str = ""
    outcome: str | None = None
    followups: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.followups = [dict(item) for item in self.followups if isinstance(item, dict)]
        self.items = [dict(item) for item in self.items if isinstance(item, dict)]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ThreadContent":
        data = require_mapping(data or {}, "content")
        return cls(
            notes=str(data.get("notes", "")),
            outcome=str(data["outcome"]) if data.get("outcome") is not None else None,
            followups=normalize_structured_list(data.get("followups", []), field_name="content.followups"),
            items=normalize_structured_list(data.get("items", []), field_name="content.items"),
        )


@dataclass
class ThreadEventRef:
    event_id: str
    role: str
    added_at: str
    added_by: str
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id is required")
        if self.role not in THREAD_EVENT_REF_ROLES:
            raise ValueError(f"role must be one of {sorted(THREAD_EVENT_REF_ROLES)}")
        if not self.added_at:
            raise ValueError("added_at is required")
        if not self.added_by:
            raise ValueError("added_by is required")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ThreadEventRef":
        payload = require_mapping(data, "event_ref")
        return cls(
            event_id=str(payload["event_id"]),
            role=str(payload["role"]),
            added_at=str(payload["added_at"]),
            added_by=str(payload["added_by"]),
            confidence=float(payload["confidence"]) if payload.get("confidence") is not None else None,
        )


@dataclass
class ThreadMeta:
    created_by: str
    updated_by: str
    revision: int = 1
    confidence: float | None = None

    def __post_init__(self) -> None:
        if not self.created_by:
            raise ValueError("created_by is required")
        if not self.updated_by:
            raise ValueError("updated_by is required")
        if self.revision < 1:
            raise ValueError("revision must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ThreadMeta":
        data = require_mapping(data or {}, "meta")
        return cls(
            created_by=str(data["created_by"]),
            updated_by=str(data["updated_by"]),
            revision=int(data.get("revision", 1)),
            confidence=float(data["confidence"]) if data.get("confidence") is not None else None,
        )


@dataclass
class ThreadRecord:
    thread_id: str
    thread_kind: str
    title: str
    status: str
    plan_time: ThreadPlanTime
    fact_time: ThreadFactTime
    content: ThreadContent
    event_refs: list[ThreadEventRef]
    meta: ThreadMeta
    first_event_at: str | None = None
    last_event_at: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.thread_id:
            raise ValueError("thread_id is required")
        if not self.thread_kind:
            raise ValueError("thread_kind is required")
        if not self.title:
            raise ValueError("title is required")
        if not self.status:
            raise ValueError("status is required")
        if not self.created_at:
            raise ValueError("created_at is required")
        if not self.updated_at:
            raise ValueError("updated_at is required")
        seen: set[str] = set()
        deduped: list[ThreadEventRef] = []
        for ref in self.event_refs:
            if ref.event_id in seen:
                continue
            seen.add(ref.event_id)
            deduped.append(ref)
        self.event_refs = deduped

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "thread_kind": self.thread_kind,
            "title": self.title,
            "status": self.status,
            "plan_time": self.plan_time.to_dict(),
            "fact_time": self.fact_time.to_dict(),
            "content": self.content.to_dict(),
            "event_refs": [ref.to_dict() for ref in self.event_refs],
            "meta": self.meta.to_dict(),
            "first_event_at": self.first_event_at,
            "last_event_at": self.last_event_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ThreadRecord":
        payload = require_mapping(data, "thread")
        return cls(
            thread_id=str(payload["thread_id"]),
            thread_kind=str(payload["thread_kind"]),
            title=str(payload["title"]),
            status=str(payload["status"]),
            plan_time=ThreadPlanTime.from_dict(require_mapping(payload.get("plan_time", {}), "plan_time")),
            fact_time=ThreadFactTime.from_dict(require_mapping(payload.get("fact_time", {}), "fact_time")),
            content=ThreadContent.from_dict(require_mapping(payload.get("content", {}), "content")),
            event_refs=[
                ThreadEventRef.from_dict(item)
                for item in payload.get("event_refs", [])
                if isinstance(item, dict)
            ],
            meta=ThreadMeta.from_dict(require_mapping(payload.get("meta", {}), "meta")),
            first_event_at=str(payload["first_event_at"]) if payload.get("first_event_at") is not None else None,
            last_event_at=str(payload["last_event_at"]) if payload.get("last_event_at") is not None else None,
            created_at=str(payload["created_at"]),
            updated_at=str(payload["updated_at"]),
        )


@dataclass
class ProjectTurnPlanTime:
    planned_start: str | None = None
    planned_end: str | None = None
    due_at: str | None = None
    all_day: bool = False
    rrule: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProjectTurnPlanTime":
        payload = ensure_allowed_keys(require_mapping(data or {}, "thread.plan_time"), {
            "planned_start",
            "planned_end",
            "due_at",
            "all_day",
            "rrule",
        }, "thread.plan_time")
        return cls(
            planned_start=require_optional_string(payload["planned_start"], "thread.plan_time.planned_start")
            if "planned_start" in payload
            else None,
            planned_end=require_optional_string(payload["planned_end"], "thread.plan_time.planned_end")
            if "planned_end" in payload
            else None,
            due_at=require_optional_string(payload["due_at"], "thread.plan_time.due_at")
            if "due_at" in payload
            else None,
            all_day=require_bool(payload["all_day"], "thread.plan_time.all_day") if "all_day" in payload else False,
            rrule=require_optional_string(payload["rrule"], "thread.plan_time.rrule")
            if "rrule" in payload
            else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectTurnFactTime:
    occurred_at: str | None = None
    completed_at: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProjectTurnFactTime":
        payload = ensure_allowed_keys(require_mapping(data or {}, "thread.fact_time"), {
            "occurred_at",
            "completed_at",
        }, "thread.fact_time")
        return cls(
            occurred_at=require_optional_string(payload["occurred_at"], "thread.fact_time.occurred_at")
            if "occurred_at" in payload
            else None,
            completed_at=require_optional_string(payload["completed_at"], "thread.fact_time.completed_at")
            if "completed_at" in payload
            else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectTurnContent:
    notes: str = ""
    outcome: str | None = None
    followups: list[dict[str, Any]] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProjectTurnContent":
        payload = ensure_allowed_keys(require_mapping(data or {}, "thread.content"), {
            "notes",
            "outcome",
            "followups",
            "items",
        }, "thread.content")
        return cls(
            notes=require_string(payload["notes"], "thread.content.notes") if "notes" in payload else "",
            outcome=require_optional_string(payload["outcome"], "thread.content.outcome")
            if "outcome" in payload
            else None,
            followups=normalize_structured_list(payload.get("followups", []), field_name="thread.content.followups"),
            items=normalize_structured_list(payload.get("items", []), field_name="thread.content.items"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectTurnThreadInput:
    title: str
    status: str
    thread_id: str | None = None
    thread_kind: str = "task"
    plan_time: ProjectTurnPlanTime = field(default_factory=ProjectTurnPlanTime)
    fact_time: ProjectTurnFactTime = field(default_factory=ProjectTurnFactTime)
    content: ProjectTurnContent = field(default_factory=ProjectTurnContent)

    def __post_init__(self) -> None:
        if not self.title:
            raise ValueError("thread.title is required")
        if not self.status:
            raise ValueError("thread.status is required")
        if not self.thread_kind:
            raise ValueError("thread.thread_kind must not be empty")

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProjectTurnThreadInput | None":
        if data is None:
            return None
        payload = ensure_allowed_keys(require_mapping(data, "thread"), {
            "thread_id",
            "thread_kind",
            "title",
            "status",
            "plan_time",
            "fact_time",
            "content",
        }, "thread")
        return cls(
            thread_id=require_optional_string(payload["thread_id"], "thread.thread_id") if "thread_id" in payload else None,
            thread_kind=require_string(payload["thread_kind"], "thread.thread_kind") if "thread_kind" in payload else "task",
            title=require_string(payload["title"], "thread.title"),
            status=require_string(payload["status"], "thread.status"),
            plan_time=ProjectTurnPlanTime.from_dict(payload.get("plan_time")),
            fact_time=ProjectTurnFactTime.from_dict(payload.get("fact_time")),
            content=ProjectTurnContent.from_dict(payload.get("content")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "thread_kind": self.thread_kind,
            "title": self.title,
            "status": self.status,
            "plan_time": self.plan_time.to_dict(),
            "fact_time": self.fact_time.to_dict(),
            "content": self.content.to_dict(),
        }


@dataclass
class ProjectTurnContext:
    source: str = "skill://timeline-memory"
    actor_id: str | None = None
    assistant_actor_id: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ProjectTurnContext":
        payload = ensure_allowed_keys(require_mapping(data or {}, "context"), {
            "source",
            "actor_id",
            "assistant_actor_id",
        }, "context")
        return cls(
            source=require_string(payload["source"], "context.source")
            if "source" in payload
            else "skill://timeline-memory",
            actor_id=require_optional_string(payload["actor_id"], "context.actor_id") if "actor_id" in payload else None,
            assistant_actor_id=require_optional_string(payload["assistant_actor_id"], "context.assistant_actor_id")
            if "assistant_actor_id" in payload
            else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectTurnInput:
    turn_id: str
    user_text: str
    assistant_text: str | None = None
    thread: ProjectTurnThreadInput | None = None
    context: ProjectTurnContext = field(default_factory=ProjectTurnContext)

    def __post_init__(self) -> None:
        if not self.turn_id:
            raise ValueError("turn_id is required")
        if ":" not in self.turn_id:
            raise ValueError("turn_id must be namespaced, for example agent:<session_id>:<turn_index>")
        if not self.user_text:
            raise ValueError("user_text is required")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectTurnInput":
        payload = ensure_allowed_keys(require_mapping(data, "project-turn input"), {
            "turn_id",
            "user_text",
            "assistant_text",
            "thread",
            "context",
        }, "project-turn input")
        return cls(
            turn_id=require_string(payload["turn_id"], "turn_id"),
            user_text=require_string(payload["user_text"], "user_text"),
            assistant_text=require_optional_string(payload["assistant_text"], "assistant_text")
            if "assistant_text" in payload
            else None,
            thread=ProjectTurnThreadInput.from_dict(payload.get("thread")),
            context=ProjectTurnContext.from_dict(payload.get("context")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "turn_id": self.turn_id,
            "user_text": self.user_text,
            "assistant_text": self.assistant_text,
            "thread": self.thread.to_dict() if self.thread is not None else None,
            "context": self.context.to_dict(),
        }

    def fingerprint(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
