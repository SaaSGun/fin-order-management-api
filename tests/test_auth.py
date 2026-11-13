async def test_missing_api_key_returns_401(client):
    response = await client.get("/instruments")
    assert response.status_code == 401


async def test_invalid_api_key_returns_401(client, api_keys):
    response = await client.get("/instruments", headers={"X-API-Key": "not-a-real-key"})
    assert response.status_code == 401


async def test_read_scope_allows_get(client, api_keys):
    response = await client.get("/instruments", headers={"X-API-Key": api_keys["read"]})
    assert response.status_code == 200
    assert "data" in response.json()


async def test_read_only_key_rejected_from_trade_endpoint(client, api_keys, seeded):
    account = seeded["account"]
    instrument = seeded["instrument"]
    response = await client.post(
        "/orders",
        headers={"X-API-Key": api_keys["read"]},
        json={
            "account_id": str(account.id),
            "instrument_id": str(instrument.id),
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": "1",
            "limit_price": "10.00",
        },
    )
    assert response.status_code == 403


async def test_trade_scope_allows_order_placement(client, api_keys, seeded):
    account = seeded["account"]
    instrument = seeded["instrument"]
    response = await client.post(
        "/orders",
        headers={"X-API-Key": api_keys["trade"], "Idempotency-Key": "auth-test-1"},
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
    assert response.json()["data"]["status"] == "NEW"


async def test_trade_scope_rejected_from_admin_endpoint(client, api_keys, seeded):
    account = seeded["account"]
    instrument = seeded["instrument"]
    order_resp = await client.post(
        "/orders",
        headers={"X-API-Key": api_keys["trade"], "Idempotency-Key": "auth-test-2"},
        json={
            "account_id": str(account.id),
            "instrument_id": str(instrument.id),
            "side": "BUY",
            "order_type": "LIMIT",
            "quantity": "1",
            "limit_price": "10.00",
        },
    )
    order_id = order_resp.json()["data"]["id"]

    response = await client.post(
        f"/orders/{order_id}/executions",
        headers={"X-API-Key": api_keys["trade"], "Idempotency-Key": "auth-test-3"},
        json={"quantity": "1", "price": "10.00"},
    )
    assert response.status_code == 403


async def test_health_requires_no_api_key(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
