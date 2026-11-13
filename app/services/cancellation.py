"""Cancelling an order: optimistic concurrency via `orders.version`, used
deliberately instead of a pessimistic `SELECT ... FOR UPDATE` here. The
two mechanics in this repo are chosen for different failure semantics:
execution.py's pessimistic lock is right for the shared hot rows (cash/
positions), where a second reader should simply wait and see the first
writer's result. A cancel racing a fill is different — if the order already
got filled, cancelling it silently would be wrong, so the conflicting writer
should fail loudly (409) and let the caller re-fetch, not block and then
apply a cancel that no longer makes sense. The conditional
`UPDATE ... WHERE version = :expected` is what gives that loud failure: zero
rows affected means someone else changed the order first.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit, snapshot
from app.enums import OrderSide, OrderStatus
from app.idempotency import IdempotencyContext, stage_response
from app.models.ledger import CashBalance, Position
from app.models.trading import Order, OrderStatusHistory
from app.observability import log_domain_event
from app.outbox import record_event

_ORDER_FIELDS = ("id", "account_id", "instrument_id", "status", "filled_quantity", "reserved_amount", "version")


class CancellationError(Exception):
    pass


class CancellationConflict(CancellationError):
    pass


async def cancel_order(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    created_by: str = "system",
    expected_version: int | None = None,
    idempotency: IdempotencyContext | None = None,
) -> Order:
    result = await session.execute(select(Order).where(Order.id == order_id))
    order = result.scalars().first()
    if order is None:
        raise CancellationError("order not found")
    if order.status not in (OrderStatus.NEW.value, OrderStatus.PARTIALLY_FILLED.value):
        raise CancellationError(f"order is {order.status}, cannot be cancelled")

    order_before = snapshot(order, _ORDER_FIELDS)
    # A caller-supplied expected_version (from an HTTP If-Match header) is
    # a genuine precondition on state the caller actually observed; falling
    # back to a version we just read ourselves (the Phase 1 behavior, still
    # used by direct/internal callers) only protects against a *race*
    # between our own read and our own write, not caller-observed staleness.
    expected_version = expected_version if expected_version is not None else order.version
    update_result = await session.execute(
        update(Order)
        .where(Order.id == order_id, Order.version == expected_version)
        .values(status=OrderStatus.CANCELLED.value, version=Order.version + 1)
    )
    if update_result.rowcount == 0:
        await session.rollback()
        log_domain_event("cancellation_conflict", order_id=str(order_id), expected_version=expected_version)
        raise CancellationConflict("order was modified concurrently (filled or already cancelled); refetch and retry")

    # Safe to use the pre-update snapshot below: a non-zero rowcount proves
    # nothing changed between our SELECT and our UPDATE.
    side = OrderSide(order.side)
    remaining = order.quantity - order.filled_quantity
    if side == OrderSide.BUY and order.reserved_amount > 0:
        cash_result = await session.execute(
            select(CashBalance)
            .where(CashBalance.account_id == order.account_id, CashBalance.currency == order.currency)
            .with_for_update()
        )
        cash_row = cash_result.scalars().first()
        cash_row.reserved_amount -= order.reserved_amount
        order.reserved_amount = Decimal(0)  # the order's own copy was never cleared in Phase 1
    elif side == OrderSide.SELL and remaining > 0:
        position_result = await session.execute(
            select(Position)
            .where(Position.account_id == order.account_id, Position.instrument_id == order.instrument_id)
            .with_for_update()
        )
        position_row = position_result.scalars().first()
        position_row.reserved_quantity -= remaining

    session.add(OrderStatusHistory(order_id=order_id, status=OrderStatus.CANCELLED.value, reason="cancelled by user"))
    await session.flush()
    await session.refresh(order)  # pick up the version bump the UPDATE above already committed-within-tx

    record_audit(
        session,
        entity_type="order",
        entity_id=order.id,
        action="cancelled",
        created_by=created_by,
        before=order_before,
        after=snapshot(order, _ORDER_FIELDS),
    )
    record_event(session, event_type="order.cancelled", payload=snapshot(order, _ORDER_FIELDS))
    if idempotency is not None:
        stage_response(session, idempotency, order, status_code=200)

    await session.commit()
    await session.refresh(order)
    log_domain_event("order_cancelled", order_id=str(order_id), account_id=str(order.account_id))
    return order
