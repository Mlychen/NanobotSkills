from __future__ import annotations

from datetime import datetime, timezone
import logging


logger = logging.getLogger(__name__)


def parse_optional_timestamp(value: str | None, *, context: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        logger.warning("Invalid timestamp for %s: %s", context, value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def timestamp_sort_key(value: str | None) -> tuple[bool, float]:
    parsed = parse_optional_timestamp(value, context="sorting")
    if parsed is None:
        return (False, float("-inf"))
    return (True, parsed.timestamp())
