"""Append-only audit trail: who did what, with full before/after state.

Called from app/services/*.py, right before that function's own commit —
in the same transaction as the business write it's recording, which is
what makes the audit trail genuinely trustworthy: a row only ever exists
here if the change it describes was actually committed, and a committed
change can never be missing its audit row.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal | uuid.UUID | datetime | date):
        return str(value)
    return value


def snapshot(obj: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    """A compact, JSON-safe dict of just the fields worth recording for
    this operation — not every column, so the blob stays readable."""
    return {field: _jsonable(getattr(obj, field)) for field in fields}


def record_audit(
    session: AsyncSession,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
    created_by: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    session.add(
        AuditLog(
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            created_by=created_by,
            before=before,
            after=after,
        )
    )
