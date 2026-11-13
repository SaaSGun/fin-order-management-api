from app.models.reference import Instrument


async def test_cursor_pagination_has_no_gaps_or_overlap(client, api_keys, session):
    symbols = ["PAG1", "PAG2", "PAG3", "PAG4", "PAG5"]
    session.add_all(
        Instrument(symbol=s, name=s, asset_class="EQUITY", currency="EUR", status="ACTIVE") for s in symbols
    )
    await session.commit()

    headers = {"X-API-Key": api_keys["read"]}
    seen: list[str] = []
    cursor = None
    for _ in range(len(symbols) + 1):  # one extra iteration as a safety net against an infinite loop
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = await client.get("/instruments", headers=headers, params=params)
        assert response.status_code == 200
        body = response.json()
        seen.extend(row["symbol"] for row in body["data"])
        pagination = body["meta"]["pagination"]
        if not pagination["has_more"]:
            assert pagination["next_cursor"] is None
            break
        cursor = pagination["next_cursor"]
    else:
        raise AssertionError("pagination never terminated")

    assert seen == sorted(symbols)


async def test_invalid_cursor_returns_400(client, api_keys):
    response = await client.get(
        "/instruments", headers={"X-API-Key": api_keys["read"]}, params={"cursor": "not-valid-base64!!"}
    )
    assert response.status_code == 400


async def test_singleton_endpoint_has_null_pagination(client, api_keys, seeded):
    account = seeded["account"]
    response = await client.get(f"/accounts/{account.id}", headers={"X-API-Key": api_keys["read"]})
    assert response.status_code == 200
    assert response.json()["meta"]["pagination"] is None
