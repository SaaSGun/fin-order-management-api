"""API-key authentication and scope-based authorization.

Callers send their key in the `X-API-Key` header. Each key is granted a
set of scopes — `read` / `trade` / `admin` — stored in the `api_keys`
table (managed via scripts/manage_api_keys.py). Routes declare what they
require via `require_scopes(...)`.

Keys are looked up by SHA-256 hash; the raw key is never persisted.
Granted scopes are cached in memory and refreshed every CACHE_TTL_SECONDS,
so a newly revoked key can stay valid on an already-warm process for up
to that long. Expiry (`expires_at`) is checked on every request instead,
so a key stops working at its expiry time.
"""

import hashlib
import time
from datetime import UTC, datetime

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.models.auth import ApiKey
from app.observability import set_client_name
from app.rate_limit import check_rate_limit

CACHE_TTL_SECONDS = 300

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
_cache: dict[str, dict] = {}
# -inf, not 0.0: time.monotonic()'s epoch is unspecified and can start near
# zero in a freshly-started process, which would make the first staleness
# check below false and serve the empty default cache forever.
_cache_loaded_at: float = float("-inf")


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()


async def _load_keys(session: AsyncSession) -> dict[str, dict]:
    result = await session.execute(select(ApiKey).where(ApiKey.active.is_(True)))
    return {
        row.key_hash: {"client_name": row.client_name, "scopes": set(row.scopes), "expires_at": row.expires_at}
        for row in result.scalars()
    }


async def _cached_keys(session: AsyncSession) -> dict[str, dict]:
    global _cache, _cache_loaded_at
    if time.monotonic() - _cache_loaded_at > CACHE_TTL_SECONDS:
        _cache = await _load_keys(session)
        _cache_loaded_at = time.monotonic()
    return _cache


def require_scopes(*required_scopes: str):
    """FastAPI dependency factory: raises 401/403/429, else returns the caller's client_name."""
    required = set(required_scopes)

    async def _dependency(
        api_key: str | None = Security(_api_key_header), session: AsyncSession = Depends(get_session)
    ) -> str:
        if api_key is None:
            raise HTTPException(status_code=401, detail="missing API key")
        record = (await _cached_keys(session)).get(hash_key(api_key))
        if record is None:
            raise HTTPException(status_code=401, detail="invalid API key")
        expires_at = record.get("expires_at")
        if expires_at is not None and expires_at <= datetime.now(UTC):
            raise HTTPException(status_code=401, detail="API key expired")
        set_client_name(record["client_name"])
        if not required.issubset(record["scopes"]):
            raise HTTPException(status_code=403, detail="insufficient scope")
        check_rate_limit(record["client_name"])
        return record["client_name"]

    return _dependency
