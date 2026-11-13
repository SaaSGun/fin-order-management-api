import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_scopes
from app.db import get_session
from app.models.ledger import CashBalance, Position
from app.models.reference import Account, Client, FxRate, Instrument
from app.pagination import build_keyset_clause, cursor_query, decode_cursor, limit_query, paginate
from app.schemas.common import Envelope, Meta, Pagination
from app.schemas.ledger import CashBalanceOut, PositionOut
from app.schemas.reference import AccountOut, AccountSummaryOut, ClientOut

router = APIRouter(dependencies=[Depends(require_scopes("read"))])


async def _get_account_or_404(session: AsyncSession, account_id: uuid.UUID) -> Account:
    account = await session.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account not found")
    return account


async def _fx_rate_to_base(session: AsyncSession, currency: str, base_currency: str) -> Decimal:
    if currency == base_currency:
        return Decimal(1)
    pair = f"{currency}{base_currency}"
    result = await session.execute(select(FxRate).where(FxRate.currency_pair == pair).order_by(FxRate.rate_date.desc()))
    rate_row = result.scalars().first()
    if rate_row is None:
        raise HTTPException(status_code=422, detail=f"no fx rate available for {pair}")
    return rate_row.rate


@router.get("/clients/{client_id}", response_model=Envelope[ClientOut])
async def get_client(client_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    client = await session.get(Client, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="client not found")
    return Envelope(data=client)


@router.get("/clients/{client_id}/accounts", response_model=Envelope[list[AccountOut]])
async def list_client_accounts(
    client_id: uuid.UUID,
    cursor: str | None = cursor_query(),
    limit: int = limit_query(),
    session: AsyncSession = Depends(get_session),
):
    decoded = decode_cursor(cursor)
    after = (decoded["id"],) if decoded else None
    clause = build_keyset_clause((Account.id,), ("asc",), after)

    stmt = select(Account).where(Account.client_id == client_id)
    if clause is not None:
        stmt = stmt.where(clause)
    stmt = stmt.order_by(Account.id.asc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars())
    page, next_cursor, has_more = paginate(rows, limit, ("id",))
    return Envelope(data=page, meta=Meta(pagination=Pagination(next_cursor=next_cursor, limit=limit, has_more=has_more)))


@router.get("/accounts/{account_id}", response_model=Envelope[AccountOut])
async def get_account(account_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    return Envelope(data=await _get_account_or_404(session, account_id))


@router.get("/accounts/{account_id}/positions", response_model=Envelope[list[PositionOut]])
async def list_positions(
    account_id: uuid.UUID,
    cursor: str | None = cursor_query(),
    limit: int = limit_query(),
    session: AsyncSession = Depends(get_session),
):
    await _get_account_or_404(session, account_id)
    decoded = decode_cursor(cursor)
    after = (decoded["instrument_id"],) if decoded else None
    clause = build_keyset_clause((Position.instrument_id,), ("asc",), after)

    stmt = select(Position).where(Position.account_id == account_id)
    if clause is not None:
        stmt = stmt.where(clause)
    stmt = stmt.order_by(Position.instrument_id.asc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars())
    page, next_cursor, has_more = paginate(rows, limit, ("instrument_id",))
    return Envelope(data=page, meta=Meta(pagination=Pagination(next_cursor=next_cursor, limit=limit, has_more=has_more)))


@router.get("/accounts/{account_id}/cash-balances", response_model=Envelope[list[CashBalanceOut]])
async def list_cash_balances(
    account_id: uuid.UUID,
    cursor: str | None = cursor_query(),
    limit: int = limit_query(),
    session: AsyncSession = Depends(get_session),
):
    await _get_account_or_404(session, account_id)
    decoded = decode_cursor(cursor)
    after = (decoded["currency"],) if decoded else None
    clause = build_keyset_clause((CashBalance.currency,), ("asc",), after)

    stmt = select(CashBalance).where(CashBalance.account_id == account_id)
    if clause is not None:
        stmt = stmt.where(clause)
    stmt = stmt.order_by(CashBalance.currency.asc()).limit(limit + 1)

    rows = list((await session.execute(stmt)).scalars())
    page, next_cursor, has_more = paginate(rows, limit, ("currency",))
    return Envelope(data=page, meta=Meta(pagination=Pagination(next_cursor=next_cursor, limit=limit, has_more=has_more)))


@router.get("/accounts/{account_id}/summary", response_model=Envelope[AccountSummaryOut])
async def account_summary(account_id: uuid.UUID, session: AsyncSession = Depends(get_session)):
    """Valued at cost, converted to the account's base currency via the
    seeded fx_rates table — not mark-to-market, since there's no live price
    feed in this demo."""
    account = await _get_account_or_404(session, account_id)

    cash_result = await session.execute(select(CashBalance).where(CashBalance.account_id == account_id))
    cash_rows = list(cash_result.scalars().all())

    position_result = await session.execute(
        select(Position, Instrument)
        .join(Instrument, Instrument.id == Position.instrument_id)
        .where(Position.account_id == account_id)
    )
    position_rows = position_result.all()

    total_cash_base = Decimal(0)
    for cash_row in cash_rows:
        rate = await _fx_rate_to_base(session, cash_row.currency, account.base_currency)
        total_cash_base += cash_row.balance * rate

    total_positions_base = Decimal(0)
    for position_row, instrument in position_rows:
        rate = await _fx_rate_to_base(session, instrument.currency, account.base_currency)
        total_positions_base += position_row.quantity * position_row.average_cost * rate

    return Envelope(
        data=AccountSummaryOut(
            account_id=account_id,
            base_currency=account.base_currency,
            total_cash=total_cash_base,
            total_positions_at_cost=total_positions_base,
            total_value=total_cash_base + total_positions_base,
        )
    )
