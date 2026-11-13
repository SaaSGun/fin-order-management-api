"""Order placement: validation, pre-trade compliance, and the buying-power /
position-availability reservation that is the main concurrency-control
showcase of this repo.

Why a reservation column, not just a balance check: two concurrent BUY
orders against the same account would both read the same unreserved
`cash_balances.balance` and both pass a plain "balance >= notional" check —
a row lock alone doesn't stop that, because nothing has changed yet for the
second reader to block on. `cash_balances.reserved_amount` (and its SELL-side
mirror, `positions.reserved_quantity`) makes the check-then-act sequence
itself a write, so the second transaction either blocks on
`SELECT ... FOR UPDATE` and then sees the first's reservation, or genuinely
can't both succeed. See app/services/execution.py and
app/services/cancellation.py for how a reservation is released.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import record_audit, snapshot
from app.enums import ComplianceResult, ComplianceRuleType, OrderSide, OrderStatus, OrderType
from app.idempotency import IdempotencyContext, stage_response
from app.models.compliance import ComplianceCheck, ConcentrationLimit, RestrictedInstrument
from app.models.ledger import CashBalance, Position
from app.models.reference import Account, Instrument
from app.models.trading import Order, OrderStatusHistory
from app.observability import log_domain_event
from app.outbox import record_event


async def place_order(
    session: AsyncSession,
    *,
    account_id: uuid.UUID,
    instrument_id: uuid.UUID,
    side: OrderSide,
    order_type: OrderType,
    quantity: Decimal,
    limit_price: Decimal | None,
    reference_price: Decimal | None,
    created_by: str = "system",
    idempotency: IdempotencyContext | None = None,
) -> Order:
    account = await session.get(Account, account_id)
    if account is None or account.status != "ACTIVE":
        raise ValueError("account not found or inactive")
    instrument = await session.get(Instrument, instrument_id)
    if instrument is None or instrument.status != "ACTIVE":
        raise ValueError("instrument not found or inactive")

    price_for_notional = limit_price if order_type == OrderType.LIMIT else reference_price
    if price_for_notional is None:
        raise ValueError(
            "limit_price is required for LIMIT orders, reference_price is required for MARKET orders"
        )
    notional = quantity * price_for_notional

    order = Order(
        account_id=account_id,
        instrument_id=instrument_id,
        side=side.value,
        order_type=order_type.value,
        quantity=quantity,
        limit_price=limit_price,
        reference_price=reference_price,
        currency=instrument.currency,
        status=OrderStatus.NEW.value,
    )
    session.add(order)
    await session.flush()  # assigns order.id, needed by the FKs below

    checks: list[ComplianceCheck] = []

    # 1. Restricted-list check (client-specific or global).
    restricted_result = await session.execute(
        select(RestrictedInstrument).where(
            RestrictedInstrument.instrument_id == instrument_id,
            (RestrictedInstrument.client_id == account.client_id) | (RestrictedInstrument.client_id.is_(None)),
        )
    )
    restriction = restricted_result.scalars().first()
    if restriction is not None:
        checks.append(
            ComplianceCheck(
                order_id=order.id,
                rule_type=ComplianceRuleType.RESTRICTED_LIST.value,
                result=ComplianceResult.FAIL.value,
                detail=restriction.reason,
            )
        )
        await _reject(session, order, checks, restriction.reason, created_by, idempotency)
        return order
    checks.append(
        ComplianceCheck(
            order_id=order.id, rule_type=ComplianceRuleType.RESTRICTED_LIST.value, result=ComplianceResult.PASS.value
        )
    )

    # 2. Concentration limit check — BUY only, a SELL only reduces concentration.
    if side == OrderSide.BUY:
        limit_row = await _get_concentration_limit(session, account_id, instrument_id, instrument.asset_class)
        if limit_row is not None:
            position = await session.get(Position, (account_id, instrument_id))
            current_qty = position.quantity if position is not None else Decimal(0)
            projected_qty = current_qty + quantity
            if projected_qty > limit_row.max_position_quantity:
                detail = f"projected position {projected_qty} exceeds limit {limit_row.max_position_quantity}"
                checks.append(
                    ComplianceCheck(
                        order_id=order.id,
                        rule_type=ComplianceRuleType.CONCENTRATION_LIMIT.value,
                        result=ComplianceResult.FAIL.value,
                        detail=detail,
                    )
                )
                await _reject(session, order, checks, detail, created_by, idempotency)
                return order
        checks.append(
            ComplianceCheck(
                order_id=order.id,
                rule_type=ComplianceRuleType.CONCENTRATION_LIMIT.value,
                result=ComplianceResult.PASS.value,
            )
        )

    # 3. Buying power / position-availability check + reservation — the core
    # concurrency-control mechanic (see module docstring).
    if side == OrderSide.BUY:
        cash_result = await session.execute(
            select(CashBalance)
            .where(CashBalance.account_id == account_id, CashBalance.currency == instrument.currency)
            .with_for_update()
        )
        cash_row = cash_result.scalars().first()
        available = (cash_row.balance - cash_row.reserved_amount) if cash_row is not None else Decimal(0)
        if cash_row is None or available < notional:
            detail = f"insufficient buying power: available {available} {instrument.currency}, needed {notional}"
            checks.append(
                ComplianceCheck(
                    order_id=order.id,
                    rule_type=ComplianceRuleType.BUYING_POWER.value,
                    result=ComplianceResult.FAIL.value,
                    detail=detail,
                )
            )
            await _reject(session, order, checks, detail, created_by, idempotency)
            return order
        cash_row.reserved_amount += notional
        order.reserved_amount = notional
    else:
        position_result = await session.execute(
            select(Position)
            .where(Position.account_id == account_id, Position.instrument_id == instrument_id)
            .with_for_update()
        )
        position_row = position_result.scalars().first()
        available_qty = (
            (position_row.quantity - position_row.reserved_quantity) if position_row is not None else Decimal(0)
        )
        if position_row is None or available_qty < quantity:
            detail = f"insufficient position: available {available_qty}, needed {quantity}"
            checks.append(
                ComplianceCheck(
                    order_id=order.id,
                    rule_type=ComplianceRuleType.BUYING_POWER.value,
                    result=ComplianceResult.FAIL.value,
                    detail=detail,
                )
            )
            await _reject(session, order, checks, detail, created_by, idempotency)
            return order
        position_row.reserved_quantity += quantity

    checks.append(
        ComplianceCheck(
            order_id=order.id, rule_type=ComplianceRuleType.BUYING_POWER.value, result=ComplianceResult.PASS.value
        )
    )

    session.add_all(checks)
    session.add(OrderStatusHistory(order_id=order.id, status=OrderStatus.NEW.value))
    await session.flush()
    # Re-read so in-memory Decimals match the DB's declared column scale
    # (e.g. a client-sent "1" becomes "1.0000") before anything below
    # snapshots or caches these values — otherwise a fresh response (built
    # from a post-commit-refreshed object) and a replayed one (cached here,
    # pre-refresh) would disagree on formatting alone.
    await session.refresh(order)
    await session.refresh(cash_row if side == OrderSide.BUY else position_row)

    reservation_snapshot = (
        {"cash_balance": snapshot(cash_row, ("account_id", "currency", "balance", "reserved_amount"))}
        if side == OrderSide.BUY
        else {"position": snapshot(position_row, ("account_id", "instrument_id", "quantity", "reserved_quantity"))}
    )
    record_audit(
        session,
        entity_type="order",
        entity_id=order.id,
        action="placed",
        created_by=created_by,
        before=None,
        after={
            "order": snapshot(order, ("id", "account_id", "instrument_id", "side", "status", "quantity", "reserved_amount")),
            **reservation_snapshot,
        },
    )
    record_event(
        session,
        event_type="order.placed",
        payload=snapshot(order, ("id", "account_id", "instrument_id", "side", "status", "quantity")),
    )
    if idempotency is not None:
        stage_response(session, idempotency, order, status_code=201)

    await session.commit()
    await session.refresh(order)
    log_domain_event(
        "order_placed",
        order_id=str(order.id),
        account_id=str(account_id),
        side=side.value,
        quantity=str(quantity),
        reserved_amount=str(order.reserved_amount),
    )
    return order


async def _get_concentration_limit(
    session: AsyncSession, account_id: uuid.UUID, instrument_id: uuid.UUID, asset_class: str
) -> ConcentrationLimit | None:
    result = await session.execute(
        select(ConcentrationLimit).where(
            ConcentrationLimit.account_id == account_id,
            (ConcentrationLimit.instrument_id == instrument_id)
            | (ConcentrationLimit.instrument_id.is_(None) & (ConcentrationLimit.asset_class == asset_class)),
        )
    )
    return result.scalars().first()


async def _reject(
    session: AsyncSession,
    order: Order,
    checks: list[ComplianceCheck],
    reason: str,
    created_by: str,
    idempotency: IdempotencyContext | None,
) -> None:
    order.status = OrderStatus.REJECTED.value
    order.rejection_reason = reason
    session.add_all(checks)
    session.add(OrderStatusHistory(order_id=order.id, status=OrderStatus.REJECTED.value, reason=reason))
    await session.flush()
    await session.refresh(order)  # see the comment in place_order()'s success path above

    record_audit(
        session,
        entity_type="order",
        entity_id=order.id,
        action="rejected",
        created_by=created_by,
        before=None,
        after=snapshot(order, ("id", "account_id", "instrument_id", "status", "rejection_reason")),
    )
    record_event(
        session,
        event_type="order.rejected",
        payload=snapshot(order, ("id", "account_id", "instrument_id", "status", "rejection_reason")),
    )
    if idempotency is not None:
        stage_response(session, idempotency, order, status_code=201)

    await session.commit()
    await session.refresh(order)
    log_domain_event("order_rejected", order_id=str(order.id), account_id=str(order.account_id), reason=reason)
