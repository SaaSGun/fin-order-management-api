"""Batch-provisions API keys for the load test in loadtest/.

Creates --count ApiKey rows (client names "<prefix>-001", "-002", ...)
and auto-detects the seeded account plus one concentration-limited and
one unrestricted instrument (from scripts/seed_demo_data.py), writing
everything loadtest/locustfile.py and loadtest/concurrency_probe.py need
into one local JSON file. Raw keys are shown only in that file - same
one-shot-reveal rule as scripts/manage_api_keys.py, never printed again.

Usage:
  python -m scripts.provision_load_test_keys --count 100
  python -m scripts.provision_load_test_keys --count 20 --out loadtest/.prod-keys.json
"""

import argparse
import asyncio
import hashlib
import json
import secrets
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionLocal
from app.models.auth import ApiKey
from app.models.compliance import ConcentrationLimit
from app.models.reference import Account, Instrument


async def _find_default_ids(session: AsyncSession) -> tuple[str, str, str]:
    account = (await session.execute(select(Account))).scalars().first()
    if account is None:
        raise SystemExit("no seeded Account found - run scripts/seed_demo_data.py first")

    limit_row = (
        await session.execute(select(ConcentrationLimit).where(ConcentrationLimit.account_id == account.id))
    ).scalars().first()
    if limit_row is None or limit_row.instrument_id is None:
        raise SystemExit(
            "expected a seeded per-instrument ConcentrationLimit on this account - check scripts/seed_demo_data.py"
        )
    limited_instrument_id = limit_row.instrument_id

    unrestricted = (
        await session.execute(select(Instrument).where(Instrument.id != limited_instrument_id))
    ).scalars().first()
    if unrestricted is None:
        raise SystemExit("need at least one seeded instrument without a concentration limit")

    return str(account.id), str(limited_instrument_id), str(unrestricted.id)


async def main(args: argparse.Namespace) -> None:
    scopes = [s.strip() for s in args.scopes.split(",") if s.strip()]
    keys: list[dict] = []

    async with SessionLocal() as session:
        account_id = args.account_id
        limited_instrument_id = args.limited_instrument_id
        unrestricted_instrument_id = args.unrestricted_instrument_id
        if not (account_id and limited_instrument_id and unrestricted_instrument_id):
            found_account_id, found_limited_id, found_unrestricted_id = await _find_default_ids(session)
            account_id = account_id or found_account_id
            limited_instrument_id = limited_instrument_id or found_limited_id
            unrestricted_instrument_id = unrestricted_instrument_id or found_unrestricted_id

        for i in range(1, args.count + 1):
            client_name = f"{args.prefix}-{i:03d}"
            raw_key = f"finoms_{secrets.token_urlsafe(32)}"
            key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
            session.add(ApiKey(key_hash=key_hash, client_name=client_name, scopes=scopes, active=True))
            keys.append({"client_name": client_name, "api_key": raw_key})
        await session.commit()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "account_id": account_id,
                "limited_instrument_id": limited_instrument_id,
                "unrestricted_instrument_id": unrestricted_instrument_id,
                "keys": keys,
            },
            indent=2,
        )
    )
    print(f"provisioned {len(keys)} API keys (scopes={scopes}) -> {out_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--prefix", default="loadtest-pm")
    parser.add_argument("--scopes", default="read,trade")
    parser.add_argument("--out", default="loadtest/.keys.json")
    parser.add_argument("--account-id", default=None)
    parser.add_argument(
        "--limited-instrument-id", default=None, help="instrument with a seeded ConcentrationLimit (default: auto-detected)"
    )
    parser.add_argument(
        "--unrestricted-instrument-id", default=None, help="instrument with no ConcentrationLimit (default: auto-detected)"
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
