"""ETag / If-Match helpers for HTTP-exposed optimistic concurrency.

A GET of a versioned resource (an order, a settlement) sets `ETag:
"<version>"`. Mutating that resource by id (cancelling an order, settling
a settlement) requires `If-Match` — the caller states the version it
last observed, and the request fails with 412 if that's no longer
current. This is what makes the guarantee genuine from the client's
point of view, rather than an internal detail the server alone benefits
from: see app/services/cancellation.py and app/services/settlement.py
for where the version check itself happens.
"""

from fastapi import HTTPException, Request


def etag_for(version: int) -> str:
    return f'"{version}"'


def require_if_match(request: Request) -> int:
    header = request.headers.get("If-Match")
    if not header:
        raise HTTPException(status_code=428, detail="If-Match header is required")
    try:
        return int(header.strip('"'))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="If-Match must be a quoted integer version, e.g. \"3\"") from exc
