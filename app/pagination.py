"""Cursor-based pagination helpers.

Every list endpoint accepts `cursor` (opaque, taken verbatim from a
previous response's `meta.pagination.next_cursor`) and `limit`, and is
sorted by a stable keyset (a unique column, or a column plus a tiebreaker
column when the primary sort column can repeat). The cursor encodes the
last-seen row's keyset values; the next page's WHERE clause resumes right
after that row instead of using OFFSET, so results stay correct even if
rows are inserted/removed between page fetches.
"""

import base64
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Query
from sqlalchemy import ColumnElement, and_, or_

DEFAULT_LIMIT = 50
MAX_LIMIT = 500


def cursor_query(
    description: str = "Opaque pagination cursor, taken from a previous response's meta.pagination.next_cursor",
) -> Any:
    return Query(None, description=description)


def limit_query(default: int = DEFAULT_LIMIT, max_limit: int = MAX_LIMIT) -> Any:
    return Query(default, ge=1, le=max_limit, description=f"max rows to return (1-{max_limit})")


def encode_cursor(fields: dict[str, Any]) -> str:
    payload = json.dumps(fields, default=str, separators=(",", ":"))
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> dict[str, Any] | None:
    if cursor is None:
        return None
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, UnicodeDecodeError) as exc:  # binascii.Error, JSONDecodeError, bad utf-8
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


def cursor_datetime(decoded: dict[str, Any] | None, field: str) -> datetime | None:
    if not decoded or field not in decoded:
        return None
    return datetime.fromisoformat(decoded[field])


def cursor_uuid(decoded: dict[str, Any] | None, field: str) -> uuid.UUID | None:
    if not decoded or field not in decoded:
        return None
    return uuid.UUID(decoded[field])


def build_keyset_clause(
    columns: tuple[Any, ...], directions: tuple[str, ...], after_values: tuple[Any, ...] | None
) -> ColumnElement | None:
    """Generalizes "WHERE (a, b) resumes after (after_a, after_b)" to any
    number of columns with independent asc/desc directions:
    `col[i] cmp after[i] AND col[0..i-1] == after[0..i-1]`, OR'd over i —
    the same shape as a hand-written 2-column keyset WHERE clause, just
    built programmatically instead of repeated per endpoint."""
    if after_values is None:
        return None
    clauses = []
    for i, (col, direction, value) in enumerate(zip(columns, directions, after_values, strict=True)):
        equalities = [columns[j] == after_values[j] for j in range(i)]
        comparison = col > value if direction == "asc" else col < value
        clauses.append(and_(*equalities, comparison))
    return or_(*clauses)


def paginate(rows: list[Any], limit: int, keyset_attrs: tuple[str, ...]) -> tuple[list[Any], str | None, bool]:
    """rows must already be sorted by the endpoint's keyset order and
    fetched with LIMIT limit+1. Trims to `limit` and builds the next
    cursor from the last kept row's `keyset_attrs` values."""
    has_more = len(rows) > limit
    page = rows[:limit]
    next_cursor = encode_cursor({attr: getattr(page[-1], attr) for attr in keyset_attrs}) if has_more and page else None
    return page, next_cursor, has_more
