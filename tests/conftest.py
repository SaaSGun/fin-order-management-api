"""Tests run against a real Postgres database, not mocks — the thing under
test is Postgres-level locking behavior, which no mock can reproduce.
Point TEST_DATABASE_URL at a throwaway database (default assumes a local
Postgres with a `fin_oms_test` database already created — see README).
"""

import hashlib
import os
from decimal import Decimal

import httpx
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.auth as auth_module
import app.rate_limit as rate_limit_module
from app.db import Base, get_session
from app.main import app
from app.models import compliance, ledger, reference, trading  # noqa: F401  (populates Base.metadata)
from app.models.auth import ApiKey
from app.models.ledger import CashBalance
from app.models.reference import Account, Client, Instrument

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://fin_oms:fin_oms@localhost:5432/fin_oms_test"
)


@pytest_asyncio.fixture(scope="session")
async def engine():
    test_engine = create_async_engine(TEST_DATABASE_URL)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield test_engine
    await test_engine.dispose()


@pytest_asyncio.fixture(autouse=True)
async def _clean_tables(engine):
    yield
    async with engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            await conn.execute(table.delete())


@pytest_asyncio.fixture(autouse=True)
def _reset_process_global_state():
    """app/auth.py's key cache and app/rate_limit.py's token buckets are
    module-global by design (see their docstrings) — reset both before
    every test so one test's API keys/rate-limit usage can't leak into
    the next."""
    auth_module._cache = {}
    auth_module._cache_loaded_at = float("-inf")
    rate_limit_module._buckets = {}
    yield


@pytest_asyncio.fixture
async def session(engine) -> AsyncSession:
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db_session:
        yield db_session


@pytest_asyncio.fixture
async def seeded(session: AsyncSession):
    """A client/account/instrument/cash set usable within a single test's
    own `session` fixture (ORM objects stay attached to that session)."""
    client = Client(name="Test Client", client_type="INDIVIDUAL", risk_profile="MODERATE", status="ACTIVE")
    session.add(client)
    await session.flush()

    account = Account(
        client_id=client.id, account_name="Test Account", base_currency="EUR", account_type="TAXABLE", status="ACTIVE"
    )
    session.add(account)

    instrument = Instrument(symbol="TEST", name="Test Co", asset_class="EQUITY", currency="EUR", status="ACTIVE")
    session.add(instrument)
    await session.flush()

    session.add(CashBalance(account_id=account.id, currency="EUR", balance=Decimal("10000.00")))
    await session.commit()

    return {"client": client, "account": account, "instrument": instrument}


@pytest_asyncio.fixture
async def seeded_account_ids(engine):
    """Same seed, but returns plain ids (not session-attached ORM objects) —
    for tests that need multiple concurrent sessions of their own, e.g.
    tests/test_concurrency.py."""
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db_session:
        client = Client(name="Concurrency Client", client_type="INDIVIDUAL", risk_profile="MODERATE", status="ACTIVE")
        db_session.add(client)
        await db_session.flush()

        account = Account(
            client_id=client.id,
            account_name="Concurrency Account",
            base_currency="EUR",
            account_type="TAXABLE",
            status="ACTIVE",
        )
        db_session.add(account)

        instrument = Instrument(symbol="CONC", name="Concurrency Co", asset_class="EQUITY", currency="EUR", status="ACTIVE")
        db_session.add(instrument)
        await db_session.flush()

        db_session.add(CashBalance(account_id=account.id, currency="EUR", balance=Decimal("10000.00")))
        await db_session.commit()

        return account.id, instrument.id


@pytest_asyncio.fixture
async def client(engine):
    """An httpx client against the real app, with `get_session` overridden
    to the test engine instead of the dev database app/db.py points at."""
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_session():
        async with session_factory() as db_session:
            yield db_session

    app.dependency_overrides[get_session] = override_get_session
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def api_keys(session: AsyncSession):
    """Raw (unhashed) API keys for each scope tier, with matching rows
    seeded into api_keys. Use with the `client` fixture."""
    raw = {"read": "test-read-key", "trade": "test-trade-key", "admin": "test-admin-key"}
    scopes = {"read": ["read"], "trade": ["read", "trade"], "admin": ["read", "trade", "admin"]}
    session.add_all(
        ApiKey(
            key_hash=hashlib.sha256(raw[tier].encode()).hexdigest(),
            client_name=f"test-{tier}",
            scopes=scopes[tier],
            active=True,
        )
        for tier in raw
    )
    await session.commit()
    return raw
