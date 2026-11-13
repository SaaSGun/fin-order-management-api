"""Idempotency-key support for the mutating endpoints.

An Idempotency-Key header is required on POST /orders,
/orders/{id}/cancel, /orders/{id}/executions, and
/settlements/{id}/settle. The response to a *successful* request is
cached, keyed by (client_name, key, method, path); replaying the exact
same request returns the cached response without touching the database
again. The same key with a *different* body is a client error (409) —
almost always a reused key, not a genuine retry.

The cache write happens inside the same service-layer transaction as
the business effect it's caching (each service accepts an optional
`idempotency: IdempotencyContext`, staged via `stage_response` right
before that service's own commit) — a second, separately-committed
write here would defeat the entire point: a crash in between could let
a retried request re-execute instead of replaying.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request
from sqlalchemy import BigInteger, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.idempotency import IdempotencyKey


@dataclass
class IdempotencyContext:
    client_name: str
    key: str
    method: str
    path: str
    request_hash: str
    response_builder: Callable[[Any], dict]


def hash_body(raw_body: bytes) -> str:
    return hashlib.sha256(raw_body).hexdigest()


def require_idempotency_key(request: Request) -> str:
    key = request.headers.get("Idempotency-Key")
    if not key:
        raise HTTPException(status_code=422, detail="Idempotency-Key header is required")
    return key


async def check_replay(
    session: AsyncSession, *, client_name: str, key: str, method: str, path: str, request_hash: str
) -> dict | None:
    """Call before invoking the service, in the same transaction. Returns
    {"status": int, "body": dict} if this exact request already completed;
    raises 409 on a reused key with a different body; returns None on a
    fresh key. A concurrent duplicate blocks on the key's lock until the
    original commits, then replays it instead of re-running the operation."""
    scope = f"{client_name}\0{key}\0{method}\0{path}".encode()
    lock_id = int.from_bytes(hashlib.sha256(scope).digest()[:8], "big", signed=True)
    await session.execute(select(func.pg_advisory_xact_lock(literal(lock_id, BigInteger))))
    result = await session.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.client_name == client_name,
            IdempotencyKey.idempotency_key == key,
            IdempotencyKey.method == method,
            IdempotencyKey.path == path,
        )
    )
    record = result.scalars().first()
    if record is None:
        return None
    if record.request_hash != request_hash:
        raise HTTPException(status_code=409, detail="Idempotency-Key reused with a different request body")
    return {"status": record.response_status, "body": record.response_body}


def stage_response(session: AsyncSession, ctx: IdempotencyContext, result: Any, status_code: int) -> None:
    """Call from within a service, right before its commit."""
    session.add(
        IdempotencyKey(
            client_name=ctx.client_name,
            idempotency_key=ctx.key,
            method=ctx.method,
            path=ctx.path,
            request_hash=ctx.request_hash,
            response_status=status_code,
            response_body=ctx.response_builder(result),
        )
    )
