from datetime import date
from decimal import Decimal

from sqlalchemy import select

from app.enums import ComplianceResult, ComplianceRuleType, OrderSide, OrderStatus, OrderType
from app.models.compliance import ComplianceCheck, RestrictedInstrument
from app.services.order_placement import place_order


async def test_buy_order_reserves_cash(session, seeded):
    account = seeded["account"]
    instrument = seeded["instrument"]

    order = await place_order(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        limit_price=Decimal("100.00"),
        reference_price=None,
    )

    assert order.status == OrderStatus.NEW.value
    assert order.reserved_amount == Decimal("1000.00")


async def test_buy_order_rejected_on_insufficient_funds(session, seeded):
    account = seeded["account"]
    instrument = seeded["instrument"]

    order = await place_order(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1000"),
        limit_price=Decimal("100.00"),
        reference_price=None,
    )

    assert order.status == OrderStatus.REJECTED.value
    assert "insufficient buying power" in order.rejection_reason


async def test_sell_order_rejected_without_a_position(session, seeded):
    account = seeded["account"]
    instrument = seeded["instrument"]

    order = await place_order(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        quantity=Decimal("5"),
        limit_price=Decimal("100.00"),
        reference_price=None,
    )

    assert order.status == OrderStatus.REJECTED.value
    assert "insufficient position" in order.rejection_reason


async def test_restricted_instrument_rejects_before_buying_power_check(session, seeded):
    account = seeded["account"]
    instrument = seeded["instrument"]
    session.add(
        RestrictedInstrument(
            instrument_id=instrument.id,
            client_id=None,
            reason="test restriction",
            effective_date=date(2024, 1, 1),
        )
    )
    await session.commit()

    order = await place_order(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("1"),
        limit_price=Decimal("1.00"),
        reference_price=None,
    )

    assert order.status == OrderStatus.REJECTED.value
    assert order.rejection_reason == "test restriction"

    checks = (
        (await session.execute(select(ComplianceCheck).where(ComplianceCheck.order_id == order.id))).scalars().all()
    )
    assert len(checks) == 1
    assert checks[0].rule_type == ComplianceRuleType.RESTRICTED_LIST.value
    assert checks[0].result == ComplianceResult.FAIL.value
