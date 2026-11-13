import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.enums import OrderSide, OrderType
from app.models.outbox import OutboxEvent
from app.services.order_placement import place_order
from scripts.outbox_relay import relay_once


class _FakePublisher:
    def __init__(self):
        self.published: list[uuid.UUID] = []

    async def publish(self, event_id, event_type, payload):
        self.published.append(event_id)


async def test_outbox_event_staged_atomically_with_business_write(session, seeded):
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

    result = await session.execute(select(OutboxEvent).where(OutboxEvent.event_type == "order.placed"))
    events = list(result.scalars())
    assert len(events) == 1
    assert events[0].payload["id"] == str(order.id)
    assert events[0].published_at is None


async def test_concurrent_relay_workers_never_double_publish(engine):
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    event_ids: list[uuid.UUID] = []
    async with session_factory() as setup_session:
        for i in range(20):
            event = OutboxEvent(event_type="test.event", payload={"i": i})
            setup_session.add(event)
            await setup_session.flush()
            event_ids.append(event.id)
        await setup_session.commit()

    publisher = _FakePublisher()

    async def worker():
        async with session_factory() as worker_session:
            total = 0
            while True:
                count = await relay_once(worker_session, publisher, batch_size=3)
                if count == 0:
                    break
                total += count
            return total

    totals = await asyncio.gather(worker(), worker())

    assert sum(totals) == 20
    assert len(publisher.published) == 20
    assert len(set(publisher.published)) == 20  # no duplicates
    assert set(publisher.published) == set(event_ids)  # every event got published
