from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_scopes
from app.db import get_session
from app.models.reference import Instrument
from app.pagination import build_keyset_clause, cursor_query, decode_cursor, limit_query, paginate
from app.schemas.common import Envelope, Meta, Pagination
from app.schemas.reference import InstrumentOut

router = APIRouter(dependencies=[Depends(require_scopes("read"))])


@router.get("/instruments", response_model=Envelope[list[InstrumentOut]])
async def list_instruments(
    cursor: str | None = cursor_query(), limit: int = limit_query(), session: AsyncSession = Depends(get_session)
):
    decoded = decode_cursor(cursor)
    after = (decoded["symbol"],) if decoded else None
    clause = build_keyset_clause((Instrument.symbol,), ("asc",), after)

    stmt = select(Instrument)
    if clause is not None:
        stmt = stmt.where(clause)
    stmt = stmt.order_by(Instrument.symbol.asc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars())
    page, next_cursor, has_more = paginate(rows, limit, ("symbol",))
    return Envelope(data=page, meta=Meta(pagination=Pagination(next_cursor=next_cursor, limit=limit, has_more=has_more)))
