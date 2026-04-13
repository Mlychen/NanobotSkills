from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
from scripts.errors import (
    TimelineInvalidArgumentError,
    TimelineMetadataConflictError,
    TimelinePartialWriteError,
    TimelineReadFailedError,
    TimelineTurnConflictError,
)
from scripts.timeline_cli import classify_cli_error


@pytest.mark.parametrize(
    ("exc", "expected_code", "expected_category", "expected_details"),
    [
        (
            TimelineInvalidArgumentError("custom invalid", details={"path": "input.json", "reason": "custom"}),
            "TM_INVALID_ARGUMENT",
            "invalid_argument",
            {"path": "input.json", "reason": "custom"},
        ),
        (
            TimelineReadFailedError("custom read failure", details={"path": "raw_events.jsonl", "line_no": 7}),
            "TM_READ_FAILED",
            "read_failed",
            {"path": "raw_events.jsonl", "line_no": 7},
        ),
        (
            TimelineTurnConflictError("custom turn conflict", details={"turn_id": "agent:test:1"}),
            "TM_TURN_CONFLICT",
            "conflict",
            {"turn_id": "agent:test:1"},
        ),
        (
            TimelineMetadataConflictError("custom metadata conflict", details={"turn_id": "agent:test:2"}),
            "TM_METADATA_CONFLICT",
            "conflict",
            {"turn_id": "agent:test:2"},
        ),
        (
            TimelinePartialWriteError("custom partial write", details={"turn_id": "agent:test:3"}),
            "TM_PARTIAL_WRITE",
            "recovery_failed",
            {"turn_id": "agent:test:3"},
        ),
    ],
)
def test_classify_cli_error_prefers_structured_exception_types(
    exc: Exception,
    expected_code: str,
    expected_category: str,
    expected_details: dict[str, object],
) -> None:
    error = classify_cli_error(exc)

    assert error.code == expected_code
    assert error.category == expected_category
    assert error.message == str(exc)
    assert error.details == expected_details
