"""Deliberately races requests against this API's concurrency guarantees
to check whether they hold under genuine parallelism, not just
sequentially. See .claude/context.md for the full write-up, including two
known gaps this is built to demonstrate precisely, not just check:

1. Idempotency race: two simultaneous requests with the identical
   Idempotency-Key don't cleanly replay - the second hits the
   idempotency_keys unique constraint after unblocking from the
   cash-balance lock, and comes back as a bare 500 (app/idempotency.py,
   app/routers/orders.py).
2. Concentration-limit race: the concentration-limit check has no
   locking/reservation, so concurrent BUY orders for the same instrument
   can collectively exceed the limit even though each individually
   passed it (app/services/order_placement.py).

The ETag/If-Match optimistic-lock check is expected to hold (it's built
on a conditional UPDATE ... WHERE version = :expected, which Postgres
serializes correctly on its own).

Usage:
  python -m loadtest.concurrency_probe --host http://localhost:8000
  python -m loadtest.concurrency_probe --host https://fin-oms-api-miatwtkcba-ez.a.run.app \
      --keys-file loadtest/.keys-prod.json
"""

import argparse
import asyncio
import json
import uuid
from pathlib import Path

import httpx

_ORDER_PAYLOAD = {
    "side": "BUY",
    "order_type": "MARKET",
}


async def idempotency_race(client: httpx.AsyncClient, account_id: str, instrument_id: str, concurrency: int) -> bool:
    key = str(uuid.uuid4())
    payload = {**_ORDER_PAYLOAD, "account_id": account_id, "instrument_id": instrument_id, "quantity": "1", "reference_price": "100.00"}
    headers = {"Idempotency-Key": key}

    responses = await asyncio.gather(*[client.post("/orders", json=payload, headers=headers) for _ in range(concurrency)])
    statuses = [r.status_code for r in responses]
    successes = [r for r in responses if r.status_code == 201]
    order_ids = {r.json()["data"]["id"] for r in successes}

    print(f"[idempotency race] {concurrency} simultaneous POST /orders, identical key+body -> statuses {statuses}")
    clean_replay = len(successes) == concurrency and len(order_ids) == 1
    if clean_replay:
        print("  PASS: every response replayed the same cached order, no duplicates, no errors.")
    elif 500 in statuses:
        print(
            "  CONFIRMED FINDING: a concurrent identical-key request came back as a bare 500 instead of a "
            "clean replay (idempotency_keys unique-constraint race - see .claude/context.md)."
        )
    else:
        print(f"  UNEXPECTED: {len(order_ids)} distinct order(s) created across {len(successes)} successful responses.")
    return clean_replay


async def etag_race(client: httpx.AsyncClient, account_id: str, instrument_id: str, concurrency: int) -> bool:
    create = await client.post(
        "/orders",
        json={**_ORDER_PAYLOAD, "account_id": account_id, "instrument_id": instrument_id, "quantity": "1", "reference_price": "100.00"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    order_id = create.json()["data"]["id"]
    get_resp = await client.get(f"/orders/{order_id}")
    etag = get_resp.headers["ETag"]

    async def fire():
        return await client.post(
            f"/orders/{order_id}/cancel", headers={"Idempotency-Key": str(uuid.uuid4()), "If-Match": etag}
        )

    responses = await asyncio.gather(*[fire() for _ in range(concurrency)])
    statuses = [r.status_code for r in responses]
    ok = statuses.count(200) == 1 and all(s in (200, 412) for s in statuses)
    print(f"[etag race] {concurrency} simultaneous cancels, identical If-Match -> statuses {statuses} ({'PASS' if ok else 'FAIL'})")
    return ok


async def concentration_limit_race(
    client: httpx.AsyncClient,
    account_id: str,
    limited_instrument_id: str,
    concurrency: int,
    per_order_qty: int,
    limit: int,
) -> bool:
    async def fire():
        return await client.post(
            "/orders",
            json={
                **_ORDER_PAYLOAD,
                "account_id": account_id,
                "instrument_id": limited_instrument_id,
                "quantity": str(per_order_qty),
                "reference_price": "150.00",
            },
            headers={"Idempotency-Key": str(uuid.uuid4())},
        )

    responses = await asyncio.gather(*[fire() for _ in range(concurrency)])
    admitted = [
        r.json()["data"] for r in responses if r.status_code == 201 and r.json()["data"]["status"] != "REJECTED"
    ]
    admitted_qty = per_order_qty * len(admitted)
    max_should_admit = limit // per_order_qty

    print(
        f"[concentration-limit race] {concurrency} simultaneous BUY orders of {per_order_qty} units each against "
        f"a {limit}-unit limit -> {len(admitted)} admitted ({admitted_qty} units total); correct behavior would "
        f"admit at most {max_should_admit}."
    )
    reproduced = len(admitted) > max_should_admit
    if reproduced:
        print("  CONFIRMED FINDING: over-admission past the concentration limit (see .claude/context.md).")
    else:
        print("  NOT REPRODUCED (unexpected - recheck app/services/order_placement.py).")

    # Clean up so repeated runs start from a clean limit.
    for order in admitted:
        order_id = order["id"]
        get_resp = await client.get(f"/orders/{order_id}")
        etag = get_resp.headers.get("ETag")
        if etag:
            await client.post(f"/orders/{order_id}/cancel", headers={"Idempotency-Key": str(uuid.uuid4()), "If-Match": etag})
    return reproduced


async def main(args: argparse.Namespace) -> None:
    config = json.loads(Path(args.keys_file).read_text())
    account_id = config["account_id"]
    limited_instrument_id = config["limited_instrument_id"]
    unrestricted_instrument_id = config["unrestricted_instrument_id"]
    api_key = config["keys"][0]["api_key"]

    async with httpx.AsyncClient(base_url=args.host, headers={"X-API-Key": api_key}, timeout=30.0) as client:
        idempotency_replayed_cleanly = await idempotency_race(client, account_id, unrestricted_instrument_id, args.concurrency)
        print()
        etag_ok = await etag_race(client, account_id, unrestricted_instrument_id, args.concurrency)
        print()
        concentration_race_reproduced = await concentration_limit_race(
            client, account_id, limited_instrument_id, args.concurrency, args.concentration_qty, args.concentration_limit
        )

    print()
    print("Summary (matches the findings documented in .claude/context.md):")
    print(f"  idempotency race   : {'PASS' if idempotency_replayed_cleanly else 'CONFIRMED FINDING (500 on concurrent retry)'}")
    print(f"  etag/If-Match race : {'PASS' if etag_ok else 'FAIL (unexpected)'}")
    print(f"  concentration race : {'CONFIRMED FINDING (over-admission)' if concentration_race_reproduced else 'PASS (unexpected)'}")

    # Exit 0 iff reality matched the documented expectations; non-zero
    # means something needs a closer look (good or bad surprise alike).
    matches_expectations = (not idempotency_replayed_cleanly) and etag_ok and concentration_race_reproduced
    raise SystemExit(0 if matches_expectations else 1)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", required=True)
    parser.add_argument("--keys-file", default="loadtest/.keys.json")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--concentration-qty", type=int, default=20)
    parser.add_argument("--concentration-limit", type=int, default=50)
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
