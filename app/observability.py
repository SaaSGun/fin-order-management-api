"""Structured JSON logging, plus a per-request log line and request-ID
propagation.

Request-scoped fields (request_id, client_name, trace) live in a context
variable that the formatter adds to every log line, including the
domain-event logs from app/services/*.py. The context holds a mutable
dict rather than being re-`.set()` so that a value set inside a
dependency (client_name, once auth resolves it) is visible to the
middleware's own log line after the handler returns — in this fully-async
app, dependency and handler run in the same asyncio task, so a plain
ContextVar would already propagate correctly, but the mutable-dict shape
is kept anyway for exact parity with how the sibling repo's sync/
threadpool version needs to work.

Written for stdout JSON lines because that's what Cloud Run (Phase 3)
lifts into Cloud Logging as a jsonPayload (severity/message/httpRequest/
logging.googleapis.com/trace are the special keys it recognizes) — this
is a no-op convenience, not a GCP dependency, before that deployment.
"""

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

logger = logging.getLogger("app.requests")
domain_logger = logging.getLogger("app.domain")

_request_ctx: ContextVar[dict | None] = ContextVar("request_ctx", default=None)


def log_domain_event(event: str, **fields: object) -> None:
    """One structured line per business-significant outcome in
    app/services/*.py — this repo's analog of the sibling's per-query
    BigQuery cost logging, but for OMS audit/ops visibility instead of
    query cost (see app/observability.py module docstring)."""
    domain_logger.info(event, extra={"fields": {"event": event, **fields}})


class CloudLoggingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"severity": record.levelname, "message": record.getMessage(), "logger": record.name}
        entry.update(_request_ctx.get() or {})
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["message"] += "\n" + self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudLoggingFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def set_client_name(client_name: str) -> None:
    ctx = _request_ctx.get()
    if ctx is not None:
        ctx["client_name"] = client_name


async def request_logging_middleware(request: Request, call_next):
    ctx = {"request_id": request.headers.get("x-request-id") or uuid.uuid4().hex, "client_name": None}
    token = _request_ctx.set(ctx)

    start = time.perf_counter()
    status, error = 500, None
    try:
        response = await call_next(request)
        status = response.status_code
    except PoolTimeoutError:
        # Every pooled DB connection stayed busy for DB_POOL_TIMEOUT: the
        # service is overloaded. The request never got a connection, so
        # nothing ran - shed it with a fast, retryable 503, not a 500.
        status = 503
        response = JSONResponse(
            {"detail": "service overloaded, retry shortly"}, status_code=503, headers={"Retry-After": "1"}
        )
    except Exception as exc:  # noqa: BLE001 - last-resort handler; traceback is logged below
        # Answer the 500 here rather than re-raising to Starlette's outer
        # ServerErrorMiddleware, so the response still gets X-Request-ID.
        error = exc
        response = PlainTextResponse("Internal Server Error", status_code=500)
    finally:
        level = logging.ERROR if status >= 500 else logging.WARNING if status >= 400 else logging.INFO
        logger.log(
            level,
            "%s %s %s",
            request.method,
            request.url.path,
            status,
            exc_info=error,
            extra={
                "fields": {
                    "event": "request",
                    "httpRequest": {
                        "requestMethod": request.method,
                        "requestUrl": str(request.url),
                        "status": status,
                        "latency": f"{time.perf_counter() - start:.3f}s",
                        "userAgent": request.headers.get("user-agent"),
                        "remoteIp": request.client.host if request.client else None,
                    },
                }
            },
        )
        _request_ctx.reset(token)

    response.headers["X-Request-ID"] = ctx["request_id"]
    return response
