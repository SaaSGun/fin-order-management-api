import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_scopes
from app.db import get_session
from app.enums import OrderType
from app.etag import etag_for, require_if_match
from app.idempotency import IdempotencyContext, check_replay, hash_body, require_idempotency_key
from app.models.compliance import ComplianceCheck
from app.models.trading import Execution, Order, OrderStatusHistory
from app.pagination import (
    build_keyset_clause,
    cursor_datetime,
    cursor_query,
    cursor_uuid,
    decode_cursor,
    limit_query,
    paginate,
)
from app.schemas.common import Envelope, Meta, Pagination
from app.schemas.trading import (
    ComplianceCheckOut,
    ExecutionCreate,
    ExecutionOut,
    OrderCreate,
    OrderOut,
    OrderStatusHistoryOut,
)
from app.services.cancellation import CancellationConflict, CancellationError, cancel_order
from app.services.execution import ExecutionError, record_execution
from app.services.order_placement import place_order

router = APIRouter()

read = Depends(require_scopes("read"))
trade = Depends(require_scopes("trade"))
admin = Depends(require_scopes("admin"))


def _order_body(order: Order) -> dict:
    return jsonable_encoder(Envelope(data=OrderOut.model_validate(order)))


def _execution_body(execution: Execution) -> dict:
    return jsonable_encoder(Envelope(data=ExecutionOut.model_validate(execution)))


@router.post("/orders", response_model=Envelope[OrderOut], status_code=201, dependencies=[trade])
async def create_order(
    payload: OrderCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    client_name: str = Depends(require_scopes("trade")),
):
    if payload.order_type == OrderType.LIMIT and payload.limit_price is None:
        raise HTTPException(status_code=422, detail="limit_price is required for LIMIT orders")
    if payload.order_type == OrderType.MARKET and payload.reference_price is None:
        raise HTTPException(status_code=422, detail="reference_price is required for MARKET orders")

    idempotency_key = require_idempotency_key(request)
    request_hash = hash_body(await request.body())
    cached = await check_replay(
        session,
        client_name=client_name,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        request_hash=request_hash,
    )
    if cached is not None:
        return JSONResponse(status_code=cached["status"], content=cached["body"])

    ctx = IdempotencyContext(
        client_name=client_name,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        request_hash=request_hash,
        response_builder=_order_body,
    )
    try:
        order = await place_order(
            session,
            account_id=payload.account_id,
            instrument_id=payload.instrument_id,
            side=payload.side,
            order_type=payload.order_type,
            quantity=payload.quantity,
            limit_price=payload.limit_price,
            reference_price=payload.reference_price,
            created_by=client_name,
            idempotency=ctx,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = JSONResponse(status_code=201, content=_order_body(order))
    response.headers["ETag"] = etag_for(order.version)
    return response


@router.get("/orders", response_model=Envelope[list[OrderOut]], dependencies=[read])
async def list_orders(
    account_id: uuid.UUID | None = Query(None, description="filter to one account"),
    cursor: str | None = cursor_query(),
    limit: int = limit_query(),
    session: AsyncSession = Depends(get_session),
):
    decoded = decode_cursor(cursor)
    after_created = cursor_datetime(decoded, "created_at")
    after_id = cursor_uuid(decoded, "id")
    clause = build_keyset_clause(
        (Order.created_at, Order.id), ("desc", "desc"), (after_created, after_id) if decoded else None
    )

    stmt = select(Order)
    if account_id is not None:
        stmt = stmt.where(Order.account_id == account_id)
    if clause is not None:
        stmt = stmt.where(clause)
    stmt = stmt.order_by(Order.created_at.desc(), Order.id.desc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars())
    page, next_cursor, has_more = paginate(rows, limit, ("created_at", "id"))
    return Envelope(data=page, meta=Meta(pagination=Pagination(next_cursor=next_cursor, limit=limit, has_more=has_more)))


@router.get("/orders/{order_id}", response_model=Envelope[OrderOut], dependencies=[read])
async def get_order(order_id: uuid.UUID, response: Response, session: AsyncSession = Depends(get_session)):
    order = await session.get(Order, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    response.headers["ETag"] = etag_for(order.version)
    return Envelope(data=order)


@router.post("/orders/{order_id}/cancel", response_model=Envelope[OrderOut], dependencies=[trade])
async def cancel(
    order_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
    client_name: str = Depends(require_scopes("trade")),
):
    idempotency_key = require_idempotency_key(request)
    expected_version = require_if_match(request)
    request_hash = hash_body(await request.body())
    cached = await check_replay(
        session,
        client_name=client_name,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        request_hash=request_hash,
    )
    if cached is not None:
        return JSONResponse(status_code=cached["status"], content=cached["body"])

    ctx = IdempotencyContext(
        client_name=client_name,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        request_hash=request_hash,
        response_builder=_order_body,
    )
    try:
        order = await cancel_order(
            session, order_id=order_id, created_by=client_name, expected_version=expected_version, idempotency=ctx
        )
    except CancellationConflict as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc
    except CancellationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = JSONResponse(status_code=200, content=_order_body(order))
    response.headers["ETag"] = etag_for(order.version)
    return response


@router.post(
    "/orders/{order_id}/executions", response_model=Envelope[ExecutionOut], status_code=201, dependencies=[admin]
)
async def create_execution(
    order_id: uuid.UUID,
    payload: ExecutionCreate,
    request: Request,
    session: AsyncSession = Depends(get_session),
    client_name: str = Depends(require_scopes("admin")),
):
    idempotency_key = require_idempotency_key(request)
    request_hash = hash_body(await request.body())
    cached = await check_replay(
        session,
        client_name=client_name,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        request_hash=request_hash,
    )
    if cached is not None:
        return JSONResponse(status_code=cached["status"], content=cached["body"])

    ctx = IdempotencyContext(
        client_name=client_name,
        key=idempotency_key,
        method="POST",
        path=request.url.path,
        request_hash=request_hash,
        response_builder=_execution_body,
    )
    try:
        execution = await record_execution(
            session,
            order_id=order_id,
            quantity=payload.quantity,
            price=payload.price,
            created_by=client_name,
            idempotency=ctx,
        )
    except ExecutionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    return JSONResponse(status_code=201, content=_execution_body(execution))


@router.get("/orders/{order_id}/executions", response_model=Envelope[list[ExecutionOut]], dependencies=[read])
async def list_executions(
    order_id: uuid.UUID,
    cursor: str | None = cursor_query(),
    limit: int = limit_query(),
    session: AsyncSession = Depends(get_session),
):
    decoded = decode_cursor(cursor)
    after_executed = cursor_datetime(decoded, "executed_at")
    after_id = cursor_uuid(decoded, "id")
    clause = build_keyset_clause(
        (Execution.executed_at, Execution.id), ("asc", "asc"), (after_executed, after_id) if decoded else None
    )

    stmt = select(Execution).where(Execution.order_id == order_id)
    if clause is not None:
        stmt = stmt.where(clause)
    stmt = stmt.order_by(Execution.executed_at.asc(), Execution.id.asc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars())
    page, next_cursor, has_more = paginate(rows, limit, ("executed_at", "id"))
    return Envelope(data=page, meta=Meta(pagination=Pagination(next_cursor=next_cursor, limit=limit, has_more=has_more)))


@router.get(
    "/orders/{order_id}/compliance-checks", response_model=Envelope[list[ComplianceCheckOut]], dependencies=[read]
)
async def list_compliance_checks(
    order_id: uuid.UUID,
    cursor: str | None = cursor_query(),
    limit: int = limit_query(),
    session: AsyncSession = Depends(get_session),
):
    decoded = decode_cursor(cursor)
    after_checked = cursor_datetime(decoded, "checked_at")
    after_id = cursor_uuid(decoded, "id")
    clause = build_keyset_clause(
        (ComplianceCheck.checked_at, ComplianceCheck.id), ("asc", "asc"), (after_checked, after_id) if decoded else None
    )

    stmt = select(ComplianceCheck).where(ComplianceCheck.order_id == order_id)
    if clause is not None:
        stmt = stmt.where(clause)
    stmt = stmt.order_by(ComplianceCheck.checked_at.asc(), ComplianceCheck.id.asc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars())
    page, next_cursor, has_more = paginate(rows, limit, ("checked_at", "id"))
    return Envelope(data=page, meta=Meta(pagination=Pagination(next_cursor=next_cursor, limit=limit, has_more=has_more)))


@router.get(
    "/orders/{order_id}/status-history", response_model=Envelope[list[OrderStatusHistoryOut]], dependencies=[read]
)
async def list_status_history(
    order_id: uuid.UUID,
    cursor: str | None = cursor_query(),
    limit: int = limit_query(),
    session: AsyncSession = Depends(get_session),
):
    decoded = decode_cursor(cursor)
    after_changed = cursor_datetime(decoded, "changed_at")
    after_id = cursor_uuid(decoded, "id")
    clause = build_keyset_clause(
        (OrderStatusHistory.changed_at, OrderStatusHistory.id),
        ("asc", "asc"),
        (after_changed, after_id) if decoded else None,
    )

    stmt = select(OrderStatusHistory).where(OrderStatusHistory.order_id == order_id)
    if clause is not None:
        stmt = stmt.where(clause)
    stmt = stmt.order_by(OrderStatusHistory.changed_at.asc(), OrderStatusHistory.id.asc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars())
    page, next_cursor, has_more = paginate(rows, limit, ("changed_at", "id"))
    return Envelope(data=page, meta=Meta(pagination=Pagination(next_cursor=next_cursor, limit=limit, has_more=has_more)))
