"""Settlement is tracked as a status lifecycle only (PENDING -> SETTLED /
FAILED) — cash and positions already moved atomically at fill time (see
app/services/execution.py); settlement here is operational/audit tracking
of the T+2 cycle, not a second money-movement step. Triggered by an
explicit admin call rather than a scheduler, to keep Phase 1 free of
background-worker infrastructure that isn't the point of this repo.

`version` plays the same HTTP-facing optimistic-concurrency role as
`Order.version` (ETag/If-Match, see app/services/cancellation.py) — but
here it's checked under the pessimistic lock already held below rather
than via a conditional UPDATE, since settling is already single-row and
already locked; no second mechanism is needed to detect the conflict,
only to let a caller *state* the version it expects.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit, snapshot
from app.enums import SettlementStatus
from app.idempotency import IdempotencyContext, stage_response
from app.models.trading import Settlement
from app.observability import log_domain_event
from app.outbox import record_event

_FIELDS = ("id", "execution_id", "status", "settled_at", "fail_reason", "version")


class SettlementError(Exception):
    pass


class SettlementConflict(SettlementError):
    pass


async def settle(
    session: AsyncSession,
    *,
    settlement_id: uuid.UUID,
    failed: bool = False,
    fail_reason: str | None = None,
    created_by: str = "system",
    expected_version: int | None = None,
    idempotency: IdempotencyContext | None = None,
) -> Settlement:
    result = await session.execute(select(Settlement).where(Settlement.id == settlement_id).with_for_update())
    settlement = result.scalars().first()
    if settlement is None:
        raise SettlementError("settlement not found")
    if settlement.status != SettlementStatus.PENDING.value:
        raise SettlementError(f"settlement is already {settlement.status}")
    if expected_version is not None and settlement.version != expected_version:
        raise SettlementConflict(f"settlement version is {settlement.version}, expected {expected_version}")

    before = snapshot(settlement, _FIELDS)

    settlement.status = SettlementStatus.FAILED.value if failed else SettlementStatus.SETTLED.value
    settlement.settled_at = datetime.now(UTC)
    settlement.version += 1
    if failed:
        settlement.fail_reason = fail_reason or "settlement failed"
    await session.flush()

    record_audit(
        session,
        entity_type="settlement",
        entity_id=settlement.id,
        action="settled" if not failed else "settlement_failed",
        created_by=created_by,
        before=before,
        after=snapshot(settlement, _FIELDS),
    )
    record_event(session, event_type="settlement.updated", payload=snapshot(settlement, _FIELDS))
    if idempotency is not None:
        stage_response(session, idempotency, settlement, status_code=200)

    await session.commit()
    await session.refresh(settlement)
    log_domain_event("settlement_updated", settlement_id=str(settlement_id), status=settlement.status)
    return settlement
