"""One-off script seeding a handful of clients/accounts/instruments so the
API has something to call against. Run after `alembic upgrade head`:

    python scripts/seed_demo_data.py
"""

import asyncio
import hashlib
import secrets
from datetime import date
from decimal import Decimal

from app.db import SessionLocal
from app.models.auth import ApiKey
from app.models.compliance import ConcentrationLimit, RestrictedInstrument
from app.models.ledger import CashBalance
from app.models.reference import Account, Client, FxRate, Instrument


def _make_api_key(client_name: str, scopes: list[str]) -> tuple[ApiKey, str]:
    raw_key = f"finoms_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return ApiKey(key_hash=key_hash, client_name=client_name, scopes=scopes, active=True), raw_key


async def seed() -> None:
    async with SessionLocal() as session:
        client = Client(name="Jane Doe", client_type="INDIVIDUAL", risk_profile="MODERATE", status="ACTIVE")
        session.add(client)
        await session.flush()

        account = Account(
            client_id=client.id,
            account_name="Jane Doe - Taxable",
            base_currency="EUR",
            account_type="TAXABLE",
            status="ACTIVE",
        )
        session.add(account)

        instruments = [
            Instrument(symbol="AAPL", name="Apple Inc.", asset_class="EQUITY", currency="USD"),
            Instrument(symbol="ASML", name="ASML Holding N.V.", asset_class="EQUITY", currency="EUR"),
            Instrument(symbol="SHELL28", name="Shell plc 2028 Bond", asset_class="BOND", currency="EUR"),
            Instrument(symbol="RESTRICTEDCO", name="Restricted Co N.V.", asset_class="EQUITY", currency="EUR"),
        ]
        session.add_all(instruments)
        await session.flush()

        session.add_all(
            [
                CashBalance(account_id=account.id, currency="EUR", balance=Decimal("100000.00")),
                CashBalance(account_id=account.id, currency="USD", balance=Decimal("20000.00")),
            ]
        )

        restricted = next(i for i in instruments if i.symbol == "RESTRICTEDCO")
        session.add(
            RestrictedInstrument(
                instrument_id=restricted.id,
                client_id=None,
                reason="Global restricted list — demo entry",
                effective_date=date(2024, 1, 1),
            )
        )

        aapl = next(i for i in instruments if i.symbol == "AAPL")
        session.add(
            ConcentrationLimit(account_id=account.id, instrument_id=aapl.id, max_position_quantity=Decimal("50"))
        )

        session.add_all(
            [
                FxRate(currency_pair="USDEUR", rate_date=date.today(), rate=Decimal("0.92")),
                FxRate(currency_pair="EURUSD", rate_date=date.today(), rate=Decimal("1.09")),
            ]
        )

        read_key, read_raw = _make_api_key("demo-read-only", ["read"])
        trade_key, trade_raw = _make_api_key("demo-trader-ops", ["read", "trade", "admin"])
        session.add_all([read_key, trade_key])

        await session.commit()

        print(f"client {client.id}")
        print(f"account {account.id}")
        for instrument in instruments:
            print(f"instrument {instrument.symbol}: {instrument.id}")
        print(f"API key (read only): {read_raw}")
        print(f"API key (read+trade+admin): {trade_raw}")


if __name__ == "__main__":
    asyncio.run(seed())
