import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ComplianceCheck(Base):
    __tablename__ = "compliance_checks"
    __table_args__ = (
        CheckConstraint(
            "rule_type IN ('RESTRICTED_LIST','CONCENTRATION_LIMIT','BUYING_POWER')",
            name="ck_compliance_checks_rule_type",
        ),
        CheckConstraint("result IN ('PASS','FAIL')", name="ck_compliance_checks_result"),
    )

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False)
    rule_type: Mapped[str] = mapped_column(String(30), nullable=False)
    result: Mapped[str] = mapped_column(String(10), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(300), nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RestrictedInstrument(Base):
    __tablename__ = "restricted_instruments"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instrument_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=False
    )
    # NULL = restricted for every client, not just one.
    client_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("clients.id", ondelete="RESTRICT"), nullable=True)
    reason: Mapped[str] = mapped_column(String(300), nullable=False)
    effective_date: Mapped[date] = mapped_column(Date(), nullable=False)


class ConcentrationLimit(Base):
    __tablename__ = "concentration_limits"

    id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False)
    # NULL instrument_id + a set asset_class = an asset-class-wide limit.
    instrument_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="RESTRICT"), nullable=True
    )
    asset_class: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Deliberately an absolute share-count cap, not a %-of-portfolio-value
    # limit — the latter needs a live valuation engine, out of scope here.
    max_position_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
