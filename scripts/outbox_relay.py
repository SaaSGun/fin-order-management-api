"""Relay unpublished outbox_events to a Publisher — see app/outbox.py for
why this exists and why LocalFilePublisher stands in for a real broker
in this environment.

Safe to run as multiple concurrent workers: `FOR UPDATE SKIP LOCKED`
means two workers polling at the same moment claim disjoint sets of
rows, so no event is ever published twice — see tests/test_outbox.py
for a test that actually proves this by running two workers at once.

Usage:
  python scripts/outbox_relay.py               # drain everything pending, then exit
  python scripts/outbox_relay.py --once         # process a single batch and exit, even if empty
  python scripts/outbox_relay.py --batch-size 10
"""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models.outbox import OutboxEvent
from app.outbox import LocalFilePublisher, Publisher

OUTBOX_STREAM_PATH = Path(__file__).resolve().parents[1] / "outbox_stream.jsonl"


async def relay_once(session: AsyncSession, publisher: Publisher, batch_size: int) -> int:
    result = await session.execute(
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.created_at)
        .limit(batch_size)
        .with_for_update(skip_locked=True)
    )
    events = list(result.scalars())
    if not events:
        await session.commit()
        return 0

    for event in events:
        await publisher.publish(event.id, event.event_type, event.payload)
        event.published_at = datetime.now(UTC)

    await session.commit()
    return len(events)


async def main(batch_size: int, run_once: bool) -> None:
    publisher = LocalFilePublisher(OUTBOX_STREAM_PATH)
    async with SessionLocal() as session:
        if run_once:
            count = await relay_once(session, publisher, batch_size)
            print(f"published {count} event(s)")
            return
        total = 0
        while True:
            count = await relay_once(session, publisher, batch_size)
            total += count
            if count == 0:
                break
        print(f"published {total} event(s)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--once", action="store_true", help="process a single batch and exit, even if empty")
    args = parser.parse_args()
    asyncio.run(main(args.batch_size, args.once))
