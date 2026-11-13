"""Per-client token-bucket rate limiting.

Each authenticated client (keyed by the client_name that require_scopes
resolves) gets a bucket of RATE_LIMIT_BURST tokens, refilled at
RATE_LIMIT_PER_SECOND. Every request spends one token; an empty bucket
means 429 + Retry-After.

State is in memory per process, not shared — a client spread over
multiple instances can get up to N x its nominal limit. Acceptable for
this project; a shared limit would need Redis.

No lock around the bucket update: every route here is `async def` on one
event loop, and this function has no `await` in it, so the body already
runs to completion without yielding control — there's nothing to race.
(The sibling repo's sync/threadpool equivalent does need a lock.)
"""

import math
import time

from fastapi import HTTPException

from app.config import get_settings

_buckets: dict[str, tuple[float, float]] = {}  # client_name -> (tokens, last refill time)


def check_rate_limit(client_name: str) -> None:
    settings = get_settings()
    rate, burst = settings.rate_limit_per_second, settings.rate_limit_burst
    now = time.monotonic()
    tokens, last = _buckets.get(client_name, (burst, now))
    tokens = min(burst, tokens + (now - last) * rate)
    allowed = tokens >= 1
    _buckets[client_name] = (tokens - 1 if allowed else tokens, now)
    if not allowed:
        retry_after = math.ceil((1 - tokens) / rate)
        raise HTTPException(status_code=429, detail="rate limit exceeded", headers={"Retry-After": str(retry_after)})
