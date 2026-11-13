import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        CheckConstraint("client_type IN ('INDIVIDUAL','ENTITY')", name="ck_clients_client_type"),
        CheckConstraint("status IN ('ACTIVE','CLOSED')", name="ck_clients_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    client_type: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_profile: Mapped[str] = mapped_column(String(20), nullable=False, default="MODERATE")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")

    accounts: Mapped[list["Account"]] = relationship(back_populates="client")


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint("account_type IN ('TAXABLE','RETIREMENT','TRUST')", name="ck_accounts_account_type"),
        CheckConstraint("status IN ('ACTIVE','CLOSED')", name="ck_accounts_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    account_type: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")

    client: Mapped["Client"] = relationship(back_populates="accounts")


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        CheckConstraint("asset_class IN ('EQUITY','BOND','FUND','CASH')", name="ck_instruments_asset_class"),
        CheckConstraint("status IN ('ACTIVE','HALTED')", name="ck_instruments_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    asset_class: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")


class FxRate(Base):
    __tablename__ = "fx_rates"

    # e.g. "USDEUR" = how many EUR one USD buys.
    currency_pair: Mapped[str] = mapped_column(String(7), primary_key=True)
    rate_date: Mapped[date] = mapped_column(Date(), primary_key=True)
    rate: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
