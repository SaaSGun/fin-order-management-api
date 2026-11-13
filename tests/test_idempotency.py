from decimal import Decimal

from sqlalchemy import select

from app.models.trading import Order


def _order_payload(account, instrument, quantity="1", limit_price="10.00"):
    return {
        "account_id": str(account.id),
        "instrument_id": str(instrument.id),
        "side": "BUY",
        "order_type": "LIMIT",
        "quantity": quantity,
        "limit_price": limit_price,
    }


async def test_missing_idempotency_key_is_422(client, api_keys, seeded):
    account, instrument = seeded["account"], seeded["instrument"]
    response = await client.post(
        "/orders", headers={"X-API-Key": api_keys["trade"]}, json=_order_payload(account, instrument)
    )
    assert response.status_code == 422


async def test_replay_returns_cached_body_without_re_executing(client, api_keys, seeded, session):
    account, instrument = seeded["account"], seeded["instrument"]
    headers = {"X-API-Key": api_keys["trade"], "Idempotency-Key": "replay-test-1"}
    payload = _order_payload(account, instrument)

    first = await client.post("/orders", headers=headers, json=payload)
    second = await client.post("/orders", headers=headers, json=payload)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()

    count = await session.execute(select(Order).where(Order.account_id == account.id))
    assert len(list(count.scalars())) == 1


async def test_same_key_different_body_is_409(client, api_keys, seeded):
    account, instrument = seeded["account"], seeded["instrument"]
    headers = {"X-API-Key": api_keys["trade"], "Idempotency-Key": "replay-test-2"}

    first = await client.post("/orders", headers=headers, json=_order_payload(account, instrument, quantity="1"))
    assert first.status_code == 201

    second = await client.post("/orders", headers=headers, json=_order_payload(account, instrument, quantity="2"))
    assert second.status_code == 409


async def test_replay_works_across_rejected_orders_too(client, api_keys, seeded, session):
    """A REJECTED order is still a successful (201) HTTP outcome — it
    should be cached and replayed just like an accepted one."""
    account, instrument = seeded["account"], seeded["instrument"]
    headers = {"X-API-Key": api_keys["trade"], "Idempotency-Key": "replay-test-3"}
    # Way more than the account's funded balance -> REJECTED.
    payload = _order_payload(account, instrument, quantity="999999", limit_price=str(Decimal("1000.00")))

    first = await client.post("/orders", headers=headers, json=payload)
    second = await client.post("/orders", headers=headers, json=payload)

    assert first.status_code == 201
    assert first.json()["data"]["status"] == "REJECTED"
    assert first.json() == second.json()

    count = await session.execute(select(Order).where(Order.account_id == account.id))
    assert len(list(count.scalars())) == 1
