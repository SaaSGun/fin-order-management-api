from decimal import Decimal

from sqlalchemy import select

from app.enums import OrderSide, OrderType
from app.models.audit import AuditLog
from app.services.cancellation import cancel_order
from app.services.execution import record_execution
from app.services.order_placement import place_order
from app.services.settlement import settle


async def _last_audit_row(session, entity_type, entity_id, action):
    result = await session.execute(
        select(AuditLog)
        .where(AuditLog.entity_type == entity_type, AuditLog.entity_id == entity_id, AuditLog.action == action)
        .order_by(AuditLog.occurred_at.desc())
    )
    return result.scalars().first()


async def test_order_placed_audit_has_actor_and_after_snapshot(session, seeded):
    account, instrument = seeded["account"], seeded["instrument"]

    order = await place_order(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        limit_price=Decimal("10.00"),
        reference_price=None,
        created_by="demo-trader",
    )

    row = await _last_audit_row(session, "order", order.id, "placed")
    assert row is not None
    assert row.created_by == "demo-trader"
    assert row.before is None
    assert row.after["order"]["status"] == "NEW"
    assert row.after["cash_balance"]["reserved_amount"] == "10.00"


async def test_execution_audit_captures_before_and_after_across_three_rows(session, seeded):
    account, instrument = seeded["account"], seeded["instrument"]

    order = await place_order(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        limit_price=Decimal("10.00"),
        reference_price=None,
        created_by="demo-trader",
    )
    await record_execution(session, order_id=order.id, quantity=Decimal("1"), price=Decimal("10.00"), created_by="demo-ops")

    row = await _last_audit_row(session, "order", order.id, "filled")
    assert row is not None
    assert row.created_by == "demo-ops"
    assert row.before["order"]["status"] == "NEW"
    assert row.after["order"]["status"] == "FILLED"
    assert row.before["cash_balance"]["balance"] == "10000.00"
    assert row.after["cash_balance"]["balance"] == "9990.00"
    assert row.before["position"]["quantity"] == "0"
    assert row.after["position"]["quantity"] == "1.0000"


async def test_cancellation_audit_shows_reservation_released(session, seeded):
    account, instrument = seeded["account"], seeded["instrument"]

    order = await place_order(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        limit_price=Decimal("10.00"),
        reference_price=None,
        created_by="demo-trader",
    )
    await cancel_order(session, order_id=order.id, created_by="demo-trader")

    row = await _last_audit_row(session, "order", order.id, "cancelled")
    assert row is not None
    assert row.created_by == "demo-trader"
    assert row.before["status"] == "NEW"
    assert row.before["reserved_amount"] == "10.00"
    assert row.after["status"] == "CANCELLED"
    assert row.after["reserved_amount"] == "0.00"


async def test_settlement_audit_shows_status_transition(session, seeded):
    account, instrument = seeded["account"], seeded["instrument"]

    order = await place_order(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        limit_price=Decimal("10.00"),
        reference_price=None,
    )
    execution = await record_execution(session, order_id=order.id, quantity=Decimal("1"), price=Decimal("10.00"))

    from app.models.trading import Settlement

    settlement_row = (
        (await session.execute(select(Settlement).where(Settlement.execution_id == execution.id))).scalars().first()
    )
    await settle(session, settlement_id=settlement_row.id, created_by="demo-ops")

    row = await _last_audit_row(session, "settlement", settlement_row.id, "settled")
    assert row is not None
    assert row.created_by == "demo-ops"
    assert row.before["status"] == "PENDING"
    assert row.after["status"] == "SETTLED"
