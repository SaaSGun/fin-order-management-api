from sqlalchemy import select

from app.models.trading import Settlement


async def _place_order(client, api_keys, account, instrument, key):
    response = await client.post(
        "/orders",
        headers={"X-API-Key": api_keys["trade"], "Idempotency-Key": key},
        json={
            "account_id": str(account.id),
            "instrument_id": str(instrument.id),
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": "1",
            "limit_price": "10.00",
        },
    )
    assert response.status_code == 201
    return response.json()["data"], response.headers["ETag"]


async def test_get_order_sets_etag(client, api_keys, seeded):
    account, instrument = seeded["account"], seeded["instrument"]
    order, create_etag = await _place_order(client, api_keys, account, instrument, "etag-test-1")
    assert create_etag == '"0"'

    response = await client.get(f"/orders/{order['id']}", headers={"X-API-Key": api_keys["read"]})
    assert response.headers["ETag"] == '"0"'


async def test_cancel_without_if_match_is_428(client, api_keys, seeded):
    account, instrument = seeded["account"], seeded["instrument"]
    order, _ = await _place_order(client, api_keys, account, instrument, "etag-test-2")

    response = await client.post(
        f"/orders/{order['id']}/cancel",
        headers={"X-API-Key": api_keys["trade"], "Idempotency-Key": "etag-test-2-cancel"},
    )
    assert response.status_code == 428


async def test_cancel_with_stale_if_match_is_412(client, api_keys, seeded):
    account, instrument = seeded["account"], seeded["instrument"]
    order, _ = await _place_order(client, api_keys, account, instrument, "etag-test-3")

    response = await client.post(
        f"/orders/{order['id']}/cancel",
        headers={
            "X-API-Key": api_keys["trade"],
            "Idempotency-Key": "etag-test-3-cancel",
            "If-Match": '"99"',
        },
    )
    assert response.status_code == 412


async def test_cancel_with_correct_if_match_succeeds_and_bumps_etag(client, api_keys, seeded):
    account, instrument = seeded["account"], seeded["instrument"]
    order, etag = await _place_order(client, api_keys, account, instrument, "etag-test-4")

    response = await client.post(
        f"/orders/{order['id']}/cancel",
        headers={
            "X-API-Key": api_keys["trade"],
            "Idempotency-Key": "etag-test-4-cancel",
            "If-Match": etag,
        },
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "CANCELLED"
    assert response.headers["ETag"] == '"1"'


async def test_settlement_etag_and_if_match(client, api_keys, seeded, session):
    account, instrument = seeded["account"], seeded["instrument"]
    order, _ = await _place_order(client, api_keys, account, instrument, "etag-test-5")

    fill_response = await client.post(
        f"/orders/{order['id']}/executions",
        headers={"X-API-Key": api_keys["admin"], "Idempotency-Key": "etag-test-5-fill"},
        json={"quantity": "1", "price": "10.00"},
    )
    assert fill_response.status_code == 201
    execution_id = fill_response.json()["data"]["id"]

    settlement_row = (
        (await session.execute(select(Settlement).where(Settlement.execution_id == execution_id))).scalars().first()
    )
    settlement_id = settlement_row.id

    get_response = await client.get(f"/settlements/{settlement_id}", headers={"X-API-Key": api_keys["read"]})
    assert get_response.status_code == 200
    assert get_response.headers["ETag"] == '"0"'

    no_match_response = await client.post(
        f"/settlements/{settlement_id}/settle",
        headers={"X-API-Key": api_keys["admin"], "Idempotency-Key": "etag-test-5-settle-1"},
        json={},
    )
    assert no_match_response.status_code == 428

    stale_response = await client.post(
        f"/settlements/{settlement_id}/settle",
        headers={"X-API-Key": api_keys["admin"], "Idempotency-Key": "etag-test-5-settle-2", "If-Match": '"5"'},
        json={},
    )
    assert stale_response.status_code == 412

    ok_response = await client.post(
        f"/settlements/{settlement_id}/settle",
        headers={"X-API-Key": api_keys["admin"], "Idempotency-Key": "etag-test-5-settle-3", "If-Match": '"0"'},
        json={},
    )
    assert ok_response.status_code == 200
    assert ok_response.json()["data"]["status"] == "SETTLED"
    assert ok_response.headers["ETag"] == '"1"'
