"""Cleans up after a load test in loadtest/.

Load tests leave open BUY orders behind, and open orders count toward
concentration limits and hold reserved cash - so until they are cancelled
the demo account rejects new orders. This script:

1. Reports exposure (held + open BUY quantity) against every
   per-instrument concentration limit - after a concurrent load test this
   is the check that the limit actually held.
2. Cancels every open order placed by a client whose name starts with
   --prefix, through the real cancel service, so reservations are released.
3. With --revoke-keys, deactivates those clients' API keys.

Usage:
  python -m scripts.cancel_load_test_orders
  python -m scripts.cancel_load_test_orders --revoke-keys
"""

import argparse
import asyncio

from sqlalchemy import text, update

from app.db import SessionLocal
from app.models.auth import ApiKey
from app.services.cancellation import cancel_order

_EXPOSURE_SQL = text(
    """
    SELECT i.symbol, cl.max_position_quantity,
           COALESCE(p.quantity, 0) AS held,
           COALESCE((SELECT SUM(o.quantity - o.filled_quantity) FROM orders o
                     WHERE o.account_id = cl.account_id AND o.instrument_id = cl.instrument_id
                       AND o.side = 'BUY' AND o.status IN ('NEW', 'PARTIALLY_FILLED')), 0) AS open_buy
    FROM concentration_limits cl
    JOIN instruments i ON i.id = cl.instrument_id
    LEFT JOIN positions p ON p.account_id = cl.account_id AND p.instrument_id = cl.instrument_id
    ORDER BY i.symbol
    """
)

_CASH_SQL = text("SELECT currency, balance, reserved_amount FROM cash_balances ORDER BY account_id, currency")

_OPEN_LOAD_TEST_ORDERS_SQL = text(
    """
    SELECT o.id FROM orders o
    JOIN audit_log a ON a.entity_type = 'order' AND a.entity_id = o.id AND a.action = 'placed'
    WHERE a.created_by LIKE :pattern AND o.status IN ('NEW', 'PARTIALLY_FILLED')
    """
)


async def _report(label: str) -> None:
    async with SessionLocal() as session:
        print(f"[{label}]")
        for symbol, limit, held, open_buy in (await session.execute(_EXPOSURE_SQL)).all():
            exposure = held + open_buy
            verdict = "within limit" if exposure <= limit else "OVER LIMIT"
            print(f"  {symbol}: held {held} + open BUY {open_buy} = {exposure} vs limit {limit} -> {verdict}")
        for currency, balance, reserved in (await session.execute(_CASH_SQL)).all():
            print(f"  cash {currency}: balance {balance}, reserved {reserved}, available {balance - reserved}")


async def main(args: argparse.Namespace) -> None:
    pattern = f"{args.prefix}%"
    await _report("before")

    async with SessionLocal() as session:
        order_ids = (await session.execute(_OPEN_LOAD_TEST_ORDERS_SQL, {"pattern": pattern})).scalars().all()
    for order_id in order_ids:
        async with SessionLocal() as session:
            await cancel_order(session, order_id=order_id, created_by=f"{args.prefix}cleanup")
    print(f"cancelled {len(order_ids)} open orders placed by '{pattern}' clients")

    if args.revoke_keys:
        async with SessionLocal() as session:
            result = await session.execute(
                update(ApiKey).where(ApiKey.client_name.like(pattern), ApiKey.active.is_(True)).values(active=False)
            )
            await session.commit()
        print(f"revoked {result.rowcount} '{pattern}' API keys (warm processes notice within 5 min)")

    await _report("after")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prefix", default="loadtest-", help="client-name prefix of the load-test API keys")
    parser.add_argument("--revoke-keys", action="store_true", help="also deactivate those clients' API keys")
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
