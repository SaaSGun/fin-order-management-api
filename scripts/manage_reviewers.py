"""Sets up evaluation access to the live demo for one reviewer.

Each reviewer gets their own client and account, funded like the seeded
demo account (EUR 100,000, USD 20,000, a 50-unit AAPL concentration
limit), so reviewers running the playground at the same time can't use
up each other's cash or limit. Their API key expires after --days. Keys
get the admin scope too, so the playground can simulate fills and
settlement on the reviewer's own orders.

Usage:
  python -m scripts.manage_reviewers create --name alice --days 7
  python -m scripts.manage_reviewers reset --name alice

`reset` cancels the reviewer's open orders, closes their account and opens
a fresh one. The ledger is never edited in place: fills already booked stay
on the closed account. The playground finds the new account by itself.

New key for an existing reviewer:
  python -m scripts.manage_api_keys create --client-name reviewer-alice --scopes read,trade,admin --days 7
Revoke early:
  python -m scripts.manage_api_keys revoke --client-name reviewer-alice
"""

import argparse
import asyncio
import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models.auth import ApiKey
from app.models.compliance import ConcentrationLimit
from app.models.ledger import CashBalance
from app.models.reference import Account, Client, Instrument
from app.models.trading import Order
from app.services.cancellation import cancel_order

_SCOPES = ["read", "trade", "admin"]


def _client_name(name: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,40}", name):
        raise SystemExit("--name: lowercase letters, digits and dashes only, e.g. alice or j-smith")
    return f"reviewer-{name}"


async def _find_client(session: AsyncSession, client_name: str) -> Client | None:
    return (await session.execute(select(Client).where(Client.name == client_name))).scalars().first()


async def _open_account(session: AsyncSession, client: Client) -> Account:
    aapl = (await session.execute(select(Instrument).where(Instrument.symbol == "AAPL"))).scalar_one()
    account = Account(
        client_id=client.id,
        account_name=f"{client.name} - Taxable",
        base_currency="EUR",
        account_type="TAXABLE",
        status="ACTIVE",
    )
    session.add(account)
    await session.flush()
    session.add_all(
        [
            CashBalance(account_id=account.id, currency="EUR", balance=Decimal("100000.00")),
            CashBalance(account_id=account.id, currency="USD", balance=Decimal("20000.00")),
            ConcentrationLimit(account_id=account.id, instrument_id=aapl.id, max_position_quantity=Decimal("50")),
        ]
    )
    return account


async def cmd_create(args: argparse.Namespace) -> None:
    client_name = _client_name(args.name)
    raw_key = f"finoms_{secrets.token_urlsafe(32)}"
    expires_at = datetime.now(UTC) + timedelta(days=args.days)

    async with SessionLocal() as session:
        if await _find_client(session, client_name) is not None:
            raise SystemExit(f"{client_name} already exists - use `reset`, or issue a new key (see --help)")
        client = Client(name=client_name, client_type="INDIVIDUAL", risk_profile="MODERATE", status="ACTIVE")
        session.add(client)
        await session.flush()
        account = await _open_account(session, client)
        session.add(
            ApiKey(
                key_hash=hashlib.sha256(raw_key.encode()).hexdigest(),
                client_name=client_name,
                scopes=_SCOPES,
                active=True,
                expires_at=expires_at,
            )
        )
        await session.commit()

    print(f"Reviewer '{args.name}' is set up. Send them these (the key is shown only once):")
    print(f"  OMS client id : {client.id}")
    print(f"  OMS API key   : {raw_key}")
    print(f"  expires       : {expires_at:%Y-%m-%d %H:%M} UTC")
    print(f"(account {account.id} - the playground finds it from the client id)")


async def cmd_reset(args: argparse.Namespace) -> None:
    client_name = _client_name(args.name)

    async with SessionLocal() as session:
        client = await _find_client(session, client_name)
        if client is None:
            raise SystemExit(f"no reviewer {client_name} - create it first")
        accounts = (
            (await session.execute(select(Account).where(Account.client_id == client.id, Account.status == "ACTIVE")))
            .scalars()
            .all()
        )
        account_ids = [a.id for a in accounts]
        open_order_ids = (
            (
                await session.execute(
                    select(Order.id).where(
                        Order.account_id.in_(account_ids), Order.status.in_(("NEW", "PARTIALLY_FILLED"))
                    )
                )
            )
            .scalars()
            .all()
        )

    # Through the real cancel service, so cash and position reservations are released.
    for order_id in open_order_ids:
        async with SessionLocal() as session:
            await cancel_order(session, order_id=order_id, created_by=f"{client_name}-reset")

    async with SessionLocal() as session:
        client = await _find_client(session, client_name)
        for account in (
            await session.execute(select(Account).where(Account.id.in_(account_ids)))
        ).scalars():
            account.status = "CLOSED"
        account = await _open_account(session, client)
        await session.commit()

    print(f"cancelled {len(open_order_ids)} open orders, closed {len(account_ids)} account(s)")
    print(f"new account {account.id} for {client_name} (client id {client.id} is unchanged)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="client, funded account and an expiring key for a reviewer")
    p_create.add_argument("--name", required=True, help="e.g. alice -> client reviewer-alice")
    p_create.add_argument("--days", type=int, default=7, help="key lifetime in days (default 7)")
    p_create.set_defaults(func=cmd_create)

    p_reset = sub.add_parser("reset", help="cancel open orders, close the account, open a fresh one")
    p_reset.add_argument("--name", required=True)
    p_reset.set_defaults(func=cmd_reset)

    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
