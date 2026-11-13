from decimal import Decimal

from sqlalchemy import select

from app.enums import OrderSide, OrderStatus, OrderType, SettlementStatus
from app.models.ledger import CashBalance, Position
from app.models.trading import Settlement
from app.services.cancellation import cancel_order
from app.services.execution import record_execution
from app.services.order_placement import place_order


async def test_full_fill_updates_position_cash_and_settlement(session, seeded):
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

    execution = await record_execution(session, order_id=order.id, quantity=Decimal("10"), price=Decimal("99.50"))

    await session.refresh(order)
    assert order.status == OrderStatus.FILLED.value
    assert order.reserved_amount == Decimal("0.00")

    position = await session.get(Position, (account.id, instrument.id))
    assert position.quantity == Decimal("10")
    assert position.average_cost == Decimal("99.5000")

    cash = await session.get(CashBalance, (account.id, instrument.currency))
    assert cash.balance == Decimal("10000.00") - Decimal("995.00")
    assert cash.reserved_amount == Decimal("0.00")

    settlement = (
        (await session.execute(select(Settlement).where(Settlement.execution_id == execution.id))).scalars().first()
    )
    assert settlement is not None
    assert settlement.status == SettlementStatus.PENDING.value


async def test_partial_fill_then_cancel_releases_remaining_reservation(session, seeded):
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

    await record_execution(session, order_id=order.id, quantity=Decimal("4"), price=Decimal("100.00"))
    cancelled = await cancel_order(session, order_id=order.id)

    assert cancelled.status == OrderStatus.CANCELLED.value

    cash = await session.get(CashBalance, (account.id, instrument.currency))
    assert cash.reserved_amount == Decimal("0.00")

    position = await session.get(Position, (account.id, instrument.id))
    assert position.quantity == Decimal("4")


async def test_sell_fill_releases_position_reservation_and_credits_cash(session, seeded):
    account = seeded["account"]
    instrument = seeded["instrument"]

    # Establish a position to sell out of, bypassing the order flow for setup.
    buy_order = await place_order(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=Decimal("10"),
        limit_price=Decimal("100.00"),
        reference_price=None,
    )
    await record_execution(session, order_id=buy_order.id, quantity=Decimal("10"), price=Decimal("100.00"))

    sell_order = await place_order(
        session,
        account_id=account.id,
        instrument_id=instrument.id,
        side=OrderSide.SELL,
        order_type=OrderType.LIMIT,
        quantity=Decimal("6"),
        limit_price=Decimal("100.00"),
        reference_price=None,
    )
    assert sell_order.status == OrderStatus.NEW.value

    await record_execution(session, order_id=sell_order.id, quantity=Decimal("6"), price=Decimal("101.00"))

    position = await session.get(Position, (account.id, instrument.id))
    assert position.quantity == Decimal("4")
    assert position.reserved_quantity == Decimal("0")

    cash = await session.get(CashBalance, (account.id, instrument.currency))
    # 10000 - 1000 (buy) + 606 (sell @ 101) = 9606
    assert cash.balance == Decimal("9606.00")
