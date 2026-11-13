import asyncio

from app.config import get_settings


async def test_burst_then_429_then_refill(client, api_keys):
    settings = get_settings()
    headers = {"X-API-Key": api_keys["read"]}

    statuses = []
    for _ in range(settings.rate_limit_burst + 1):
        response = await client.get("/instruments", headers=headers)
        statuses.append(response.status_code)

    assert statuses[:-1] == [200] * settings.rate_limit_burst
    assert statuses[-1] == 429

    retry_after = response.headers.get("Retry-After")
    assert retry_after is not None

    await asyncio.sleep(1 / settings.rate_limit_per_second + 0.1)
    response = await client.get("/instruments", headers=headers)
    assert response.status_code == 200


async def test_health_endpoint_is_never_rate_limited(client, api_keys):
    settings = get_settings()
    for _ in range(settings.rate_limit_burst + 5):
        response = await client.get("/health")
        assert response.status_code == 200


async def test_failed_auth_does_not_consume_a_token(client):
    for _ in range(5):
        response = await client.get("/instruments", headers={"X-API-Key": "wrong"})
        assert response.status_code == 401
