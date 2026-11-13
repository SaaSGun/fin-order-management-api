"""Recording a fill: one multi-table atomic transaction (order, reservation
release, position, cash, cash ledger, settlement) — see the module-level
note in app/services/order_placement.py for the reservation mechanic this
releases, and app/services/cancellation.py for the complementary optimistic
lock used on the order's own state transitions. Here the order row is
locked pessimistically (`SELECT ... FOR UPDATE`) because fills are exactly
the case where a *second reader* (a racing cancel or a second fill) must
block and see the first fill's result, not fail loudly and retry.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit, snapshot
from app.enums import CashMovementType, OrderSide, OrderStatus, SettlementStatus
from app.idempotency import IdempotencyContext, stage_response
from app.models.ledger import CashBalance, CashMovement, Position
from app.models.trading import Execution, Order, OrderStatusHistory, Settlement
from app.observability import log_domain_event
from app.outbox import record_event

_ORDER_FIELDS = ("id", "account_id", "instrument_id", "status", "filled_quantity", "reserved_amount", "version")
_CASH_FIELDS = ("account_id", "currency", "balance", "reserved_amount")
_POSITION_FIELDS = ("account_id", "instrument_id", "quantity", "reserved_quantity", "average_cost")


class ExecutionError(Exception):
    pass


async def record_execution(
    session: AsyncSession,
    *,
    order_id: uuid.UUID,
    quantity: Decimal,
    price: Decimal,
    created_by: str = "system",
    idempotency: IdempotencyContext | None = None,
) -> Execution:
    order_result = await session.execute(select(Order).where(Order.id == order_id).with_for_update())
    order = order_result.scalars().first()
    if order is None:
        raise ExecutionError("order not found")
    if order.status not in (OrderStatus.NEW.value, OrderStatus.PARTIALLY_FILLED.value):
        raise ExecutionError(f"order is {order.status}, cannot be filled")

    remaining_before = order.quantity - order.filled_quantity
    if quantity <= 0 or quantity > remaining_before:
        raise ExecutionError(f"fill quantity {quantity} exceeds remaining {remaining_before}")

    is_final_fill = quantity == remaining_before
    side = OrderSide(order.side)

    order_before = snapshot(order, _ORDER_FIELDS)

    execution = Execution(order_id=order.id, quantity=quantity, price=price, currency=order.currency)
    session.add(execution)
    await session.flush()  # assigns execution.id, needed by cash_movements/settlements below

    # Both rows are guaranteed to exist: place_order() only reaches NEW/
    # PARTIALLY_FILLED (as opposed to REJECTED) after having found and
    # reserved against them.
    cash_result = await session.execute(
        select(CashBalance)
        .where(CashBalance.account_id == order.account_id, CashBalance.currency == order.currency)
        .with_for_update()
    )
    cash_row = cash_result.scalars().first()
    cash_before = snapshot(cash_row, _CASH_FIELDS)

    position_result = await session.execute(
        select(Position)
        .where(Position.account_id == order.account_id, Position.instrument_id == order.instrument_id)
        .with_for_update()
    )
    position_row = position_result.scalars().first()
    if position_row is None:
        position_row = Position(account_id=order.account_id, instrument_id=order.instrument_id)
        session.add(position_row)
        await session.flush()
    position_before = snapshot(position_row, _POSITION_FIELDS)

    if side == OrderSide.BUY:
        # Release this fill's share of the order's remaining reservation. On
        # the fill that completes the order, release whatever is left
        # exactly (avoids rounding dust from the proportional formula).
        release = order.reserved_amount if is_final_fill else (order.reserved_amount * quantity / remaining_before)
        cash_row.reserved_amount -= release
        order.reserved_amount -= release
        cash_row.balance -= quantity * price
        movement_amount = -(quantity * price)

        new_quantity = position_row.quantity + quantity
        if new_quantity > 0:
            position_row.average_cost = (
                (position_row.quantity * position_row.average_cost) + (quantity * price)
            ) / new_quantity
        position_row.quantity = new_quantity
    else:
        position_row.reserved_quantity -= quantity
        position_row.quantity -= quantity
        cash_row.balance += quantity * price
        movement_amount = quantity * price

    order.filled_quantity += quantity
    order.status = OrderStatus.FILLED.value if is_final_fill else OrderStatus.PARTIALLY_FILLED.value
    order.version += 1

    session.add(
        CashMovement(
            account_id=order.account_id,
            currency=order.currency,
            amount=movement_amount,
            movement_type=CashMovementType.TRADE_SETTLEMENT.value,
            related_execution_id=execution.id,
        )
    )
    session.add(OrderStatusHistory(order_id=order.id, status=order.status))
    session.add(
        Settlement(
            execution_id=execution.id,
            status=SettlementStatus.PENDING.value,
            expected_settlement_date=date.today() + timedelta(days=2),
        )
    )
    await session.flush()
    # Re-read so in-memory Decimals match each column's declared scale
    # (e.g. a client-sent price of "10.00" on a NUMERIC(18,4) column
    # becomes "10.0000") before anything below snapshots or caches these
    # values — see the matching comment in order_placement.py.
    await session.refresh(execution)
    await session.refresh(order)
    await session.refresh(cash_row)
    await session.refresh(position_row)

    record_audit(
        session,
        entity_type="order",
        entity_id=order.id,
        action="filled",
        created_by=created_by,
        before={"order": order_before, "cash_balance": cash_before, "position": position_before},
        after={
            "order": snapshot(order, _ORDER_FIELDS),
            "cash_balance": snapshot(cash_row, _CASH_FIELDS),
            "position": snapshot(position_row, _POSITION_FIELDS),
        },
    )
    record_event(
        session,
        event_type="execution.recorded",
        payload=snapshot(execution, ("id", "order_id", "quantity", "price", "currency")),
    )
    if idempotency is not None:
        stage_response(session, idempotency, execution, status_code=201)

    await session.commit()
    await session.refresh(execution)
    log_domain_event(
        "execution_recorded",
        execution_id=str(execution.id),
        order_id=str(order.id),
        quantity=str(quantity),
        price=str(price),
        order_status=order.status,
    )
    return execution
