"""Deliberately races requests against this API's concurrency guarantees
to check that they hold under genuine parallelism, not just sequentially:

1. Idempotency race: simultaneous requests with the identical
   Idempotency-Key must all replay the same single order, never 500
   (app/idempotency.py's per-key advisory lock).
2. ETag race: simultaneous cancels with the same If-Match must yield
   exactly one 200, the rest 412 (app/services/cancellation.py).
3. Concentration-limit race: simultaneous BUY orders that each fit the
   limit alone but not together must not all be admitted
   (app/services/order_placement.py's locked limit row).

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


async def warm_up(client: httpx.AsyncClient, concurrency: int) -> None:
    """Opens the racing connections before any race, with retries: the races
    then run on warm keep-alive connections, so requests really arrive
    together and a flaky resolver can't fail one racer mid-race."""
    for _ in range(5):
        try:
            await asyncio.gather(*[client.get("/health") for _ in range(concurrency)])
            return
        except httpx.ConnectError as exc:
            print(f"warm-up: {exc!r}, retrying")
            await asyncio.sleep(2)
    raise SystemExit("could not open connections to the API")


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
    else:
        print(f"  FAIL: {len(order_ids)} distinct order(s) across {len(successes)}/{concurrency} successful responses.")
    return clean_replay


async def etag_race(
    client: httpx.AsyncClient, account_id: str, instrument_id: str, concurrency: int, api_keys: list[str]
) -> bool:
    create = await client.post(
        "/orders",
        json={**_ORDER_PAYLOAD, "account_id": account_id, "instrument_id": instrument_id, "quantity": "1", "reference_price": "100.00"},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    order_id = create.json()["data"]["id"]
    get_resp = await client.get(f"/orders/{order_id}")
    etag = get_resp.headers["ETag"]

    async def fire(i: int):
        return await client.post(
            f"/orders/{order_id}/cancel",
            headers={"Idempotency-Key": str(uuid.uuid4()), "If-Match": etag, "X-API-Key": api_keys[i % len(api_keys)]},
        )

    responses = await asyncio.gather(*[fire(i) for i in range(concurrency)])
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
    api_keys: list[str],
) -> bool:
    async def fire(i: int):
        return await client.post(
            "/orders",
            json={
                **_ORDER_PAYLOAD,
                "account_id": account_id,
                "instrument_id": limited_instrument_id,
                "quantity": str(per_order_qty),
                "reference_price": "150.00",
            },
            headers={"Idempotency-Key": str(uuid.uuid4()), "X-API-Key": api_keys[i % len(api_keys)]},
        )

    responses = await asyncio.gather(*[fire(i) for i in range(concurrency)])
    admitted = [
        r.json()["data"] for r in responses if r.status_code == 201 and r.json()["data"]["status"] != "REJECTED"
    ]
    admitted_qty = per_order_qty * len(admitted)
    max_should_admit = limit // per_order_qty

    print(
        f"[concentration-limit race] {concurrency} simultaneous BUY orders of {per_order_qty} units each against "
        f"a {limit}-unit limit -> {len(admitted)} admitted ({admitted_qty} units total); at most "
        f"{max_should_admit} may be admitted, fewer if open orders already use some of the limit."
    )
    ok = len(admitted) <= max_should_admit
    print("  PASS: limit held." if ok else "  FAIL: over-admission past the concentration limit.")

    # Clean up so repeated runs start from a clean limit.
    for order in admitted:
        order_id = order["id"]
        get_resp = await client.get(f"/orders/{order_id}")
        etag = get_resp.headers.get("ETag")
        if etag:
            await client.post(f"/orders/{order_id}/cancel", headers={"Idempotency-Key": str(uuid.uuid4()), "If-Match": etag})
    return ok


async def main(args: argparse.Namespace) -> None:
    config = json.loads(Path(args.keys_file).read_text())
    account_id = config["account_id"]
    limited_instrument_id = config["limited_instrument_id"]
    unrestricted_instrument_id = config["unrestricted_instrument_id"]
    # The idempotency race stays on one key (a retrying client - keys are
    # scoped per client); the other races spread across keys, one PM each,
    # which also keeps them under the per-key rate limit at higher concurrency.
    api_keys = [k["api_key"] for k in config["keys"]]

    limits = httpx.Limits(keepalive_expiry=60)
    async with httpx.AsyncClient(
        base_url=args.host, headers={"X-API-Key": api_keys[0]}, timeout=30.0, limits=limits
    ) as client:
        await warm_up(client, args.concurrency)
        idempotency_ok = await idempotency_race(client, account_id, unrestricted_instrument_id, args.concurrency)
        print()
        etag_ok = await etag_race(client, account_id, unrestricted_instrument_id, args.concurrency, api_keys)
        print()
        concentration_ok = await concentration_limit_race(
            client,
            account_id,
            limited_instrument_id,
            args.concurrency,
            args.concentration_qty,
            args.concentration_limit,
            api_keys,
        )

    print()
    print("Summary:")
    print(f"  idempotency race   : {'PASS' if idempotency_ok else 'FAIL'}")
    print(f"  etag/If-Match race : {'PASS' if etag_ok else 'FAIL'}")
    print(f"  concentration race : {'PASS' if concentration_ok else 'FAIL'}")
    raise SystemExit(0 if idempotency_ok and etag_ok and concentration_ok else 1)


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
