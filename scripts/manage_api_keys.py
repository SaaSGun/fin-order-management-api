"""Admin CLI for issuing/listing/revoking API keys for fin-oms-api.

Only the SHA-256 hash of a key is ever stored - the raw key is printed
once at creation time and cannot be retrieved again.

Usage:
  python -m scripts.manage_api_keys create --client-name "demo-trader" --scopes read,trade --days 7
  python -m scripts.manage_api_keys list
  python -m scripts.manage_api_keys revoke --client-name "demo-trader"
"""

import argparse
import asyncio
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.db import SessionLocal
from app.models.auth import ApiKey

VALID_SCOPES = {"read", "trade", "admin"}


async def cmd_create(args: argparse.Namespace) -> None:
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    invalid = set(scopes) - VALID_SCOPES
    if invalid:
        raise SystemExit(f"invalid scopes: {sorted(invalid)}. valid: {sorted(VALID_SCOPES)}")

    raw_key = f"finoms_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    expires_at = datetime.now(UTC) + timedelta(days=args.days) if args.days is not None else None

    async with SessionLocal() as session:
        session.add(
            ApiKey(key_hash=key_hash, client_name=args.client_name, scopes=scopes, active=True, expires_at=expires_at)
        )
        await session.commit()

    print("API key created - copy it now, it will not be shown again:")
    print(raw_key)
    print(f"client: {args.client_name}  scopes: {scopes}  expires_at: {expires_at or 'never'}")


async def cmd_list(_args: argparse.Namespace) -> None:
    async with SessionLocal() as session:
        result = await session.execute(select(ApiKey).order_by(ApiKey.created_at.desc()))
        for row in result.scalars():
            print(
                f"{row.client_name:<30} active={row.active}  scopes={row.scopes}  "
                f"created_at={row.created_at}  expires_at={row.expires_at or 'never'}"
            )


async def cmd_revoke(args: argparse.Namespace) -> None:
    async with SessionLocal() as session:
        await session.execute(update(ApiKey).where(ApiKey.client_name == args.client_name).values(active=False))
        await session.commit()
    print(f"revoked all keys for client '{args.client_name}' (takes up to 5 min to take effect on warm processes)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_create = sub.add_parser("create", help="issue a new API key")
    p_create.add_argument("--client-name", required=True)
    p_create.add_argument("--scopes", required=True, help="comma-separated, e.g. read,trade")
    p_create.add_argument("--days", type=int, default=None, help="expire the key after N days (default: never)")
    p_create.set_defaults(func=cmd_create)

    p_list = sub.add_parser("list", help="list all key records (hashes are never shown)")
    p_list.set_defaults(func=cmd_list)

    p_revoke = sub.add_parser("revoke", help="deactivate all keys for a client")
    p_revoke.add_argument("--client-name", required=True)
    p_revoke.set_defaults(func=cmd_revoke)

    args = parser.parse_args()
    asyncio.run(args.func(args))


if __name__ == "__main__":
    main()
