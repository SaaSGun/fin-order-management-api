import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_scopes
from app.db import get_session
from app.etag import etag_for, require_if_match
from app.idempotency import IdempotencyContext, check_replay, hash_body, require_idempotency_key
from app.models.trading import Settlement
from app.schemas.common import Envelope
from app.schemas.trading import SettlementOut
from app.services.settlement import SettlementConflict, SettlementError, settle

router = APIRouter()

read = Depends(require_scopes("read"))
admin = Depends(require_scopes("admin"))


class SettleRequest(BaseModel):
    failed: bool = False
    fail_reason: str | None = None


def _settlement_body(settlement: Settlement) -> dict:
    return jsonable_encoder(Envelope(data=SettlementOut.model_validate(settlement)))


@router.get("/settlements/{settlement_id}", response_model=Envelope[SettlementOut], dependencies=[read])
async def get_settlement(settlement_id: uuid.UUID, response: Response, session: AsyncSession = Depends(get_session)):
    settlement = await session.get(Settlement, settlement_id)
    if settlement is None:
        raise HTTPException(status_code=404, detail="settlement not found")
    response.headers["ETag"] = etag_for(settlement.version)
    return Envelope(data=settlement)


@router.post("/settlements/{settlement_id}/settle", response_model=Envelope[SettlementOut], dependencies=[admin])
async def settle_settlement(
    settlement_id: uuid.UUID,
    payload: SettleRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    client_name: str = Depends(require_scopes("admin")),
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
        response_builder=_settlement_body,
    )
    try:
        settlement = await settle(
            session,
            settlement_id=settlement_id,
            failed=payload.failed,
            fail_reason=payload.fail_reason,
            created_by=client_name,
            expected_version=expected_version,
            idempotency=ctx,
        )
    except SettlementConflict as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc
    except SettlementError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    response = JSONResponse(status_code=200, content=_settlement_body(settlement))
    response.headers["ETag"] = etag_for(settlement.version)
    return response
