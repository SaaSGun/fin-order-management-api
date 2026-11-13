"""The centerpiece tests: fire genuinely concurrent requests at the same
account/order and assert the DB-level locking actually prevents the bug a
naive implementation would have. See app/services/order_placement.py and
app/services/cancellation.py for the mechanics under test.
"""

import asyncio
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.enums import OrderSide, OrderStatus, OrderType
from app.services.cancellation import CancellationConflict, CancellationError, cancel_order
from app.services.execution import ExecutionError, record_execution
from app.services.order_placement import place_order


async def test_concurrent_orders_cannot_overdraw_an_account(engine, seeded_account_ids):
    """Account is funded with 10,000 EUR. Two concurrent BUY orders each
    worth 8,000 EUR are fired at once — at most one may be accepted."""
    account_id, instrument_id = seeded_account_ids
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def attempt():
        async with session_factory() as attempt_session:
            return await place_order(
                attempt_session,
                account_id=account_id,
                instrument_id=instrument_id,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                quantity=Decimal("80"),
                limit_price=Decimal("100.00"),
                reference_price=None,
            )

    order_a, order_b = await asyncio.gather(attempt(), attempt())

    statuses = [order_a.status, order_b.status]
    assert statuses.count(OrderStatus.NEW.value) == 1
    assert statuses.count(OrderStatus.REJECTED.value) == 1


async def test_cancel_racing_fill_exactly_one_wins(engine, seeded_account_ids):
    """A cancel and a fill fired at the same order at the same time — the
    optimistic version check must let exactly one of them through."""
    account_id, instrument_id = seeded_account_ids
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as setup_session:
        order = await place_order(
            setup_session,
            account_id=account_id,
            instrument_id=instrument_id,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=Decimal("10"),
            limit_price=Decimal("100.00"),
            reference_price=None,
        )
        order_id = order.id

    outcomes: list[str] = []

    async def do_cancel():
        async with session_factory() as cancel_session:
            try:
                await cancel_order(cancel_session, order_id=order_id)
                outcomes.append("cancel_ok")
            except (CancellationConflict, CancellationError):
                outcomes.append("cancel_failed")

    async def do_fill():
        async with session_factory() as fill_session:
            try:
                await record_execution(fill_session, order_id=order_id, quantity=Decimal("10"), price=Decimal("100.00"))
                outcomes.append("fill_ok")
            except ExecutionError:
                outcomes.append("fill_failed")

    await asyncio.gather(do_cancel(), do_fill())

    successes = [o for o in outcomes if o.endswith("_ok")]
    assert len(successes) == 1
