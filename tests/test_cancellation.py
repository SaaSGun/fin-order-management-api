from decimal import Decimal

import pytest

from app.enums import OrderSide, OrderStatus, OrderType
from app.services.cancellation import CancellationError, cancel_order
from app.services.execution import record_execution
from app.services.order_placement import place_order


async def test_cancel_new_order_releases_reservation(session, seeded):
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

    cancelled = await cancel_order(session, order_id=order.id)
    assert cancelled.status == OrderStatus.CANCELLED.value


async def test_cannot_cancel_a_fully_filled_order(session, seeded):
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
    await record_execution(session, order_id=order.id, quantity=Decimal("10"), price=Decimal("100.00"))

    with pytest.raises(CancellationError):
        await cancel_order(session, order_id=order.id)
