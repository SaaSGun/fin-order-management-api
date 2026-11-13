"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-12

"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "clients",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("client_type", sa.String(20), nullable=False),
        sa.Column("risk_profile", sa.String(20), nullable=False, server_default="MODERATE"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.CheckConstraint("client_type IN ('INDIVIDUAL','ENTITY')", name="ck_clients_client_type"),
        sa.CheckConstraint("status IN ('ACTIVE','CLOSED')", name="ck_clients_status"),
    )

    op.create_table(
        "instruments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.String(20), nullable=False, unique=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("asset_class", sa.String(20), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.CheckConstraint("asset_class IN ('EQUITY','BOND','FUND','CASH')", name="ck_instruments_asset_class"),
        sa.CheckConstraint("status IN ('ACTIVE','HALTED')", name="ck_instruments_status"),
    )

    op.create_table(
        "fx_rates",
        sa.Column("currency_pair", sa.String(7), primary_key=True),
        sa.Column("rate_date", sa.Date(), primary_key=True),
        sa.Column("rate", sa.Numeric(18, 8), nullable=False),
    )

    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("account_name", sa.String(200), nullable=False),
        sa.Column("base_currency", sa.String(3), nullable=False),
        sa.Column("account_type", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.CheckConstraint("account_type IN ('TAXABLE','RETIREMENT','TRUST')", name="ck_accounts_account_type"),
        sa.CheckConstraint("status IN ('ACTIVE','CLOSED')", name="ck_accounts_status"),
    )
    op.create_index("ix_accounts_client_id", "accounts", ["client_id"])

    op.create_table(
        "cash_balances",
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("currency", sa.String(3), primary_key=True),
        sa.Column("balance", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("reserved_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "positions",
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column(
            "instrument_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instruments.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("reserved_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("average_cost", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "instrument_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instruments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("order_type", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("limit_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("reference_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="NEW"),
        sa.Column("filled_quantity", sa.Numeric(18, 4), nullable=False, server_default="0"),
        sa.Column("reserved_amount", sa.Numeric(18, 2), nullable=False, server_default="0"),
        sa.Column("rejection_reason", sa.String(300), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("side IN ('BUY','SELL')", name="ck_orders_side"),
        sa.CheckConstraint("order_type IN ('MARKET','LIMIT')", name="ck_orders_order_type"),
        sa.CheckConstraint(
            "status IN ('NEW','PARTIALLY_FILLED','FILLED','CANCELLED','REJECTED')", name="ck_orders_status"
        ),
        sa.CheckConstraint("quantity > 0", name="ck_orders_quantity_positive"),
    )
    op.create_index("ix_orders_account_id", "orders", ["account_id"])
    op.create_index("ix_orders_instrument_id", "orders", ["instrument_id"])

    op.create_table(
        "order_status_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reason", sa.String(300), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_order_status_history_order_id", "order_status_history", ["order_id"])

    op.create_table(
        "compliance_checks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("rule_type", sa.String(30), nullable=False),
        sa.Column("result", sa.String(10), nullable=False),
        sa.Column("detail", sa.String(300), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "rule_type IN ('RESTRICTED_LIST','CONCENTRATION_LIMIT','BUYING_POWER')",
            name="ck_compliance_checks_rule_type",
        ),
        sa.CheckConstraint("result IN ('PASS','FAIL')", name="ck_compliance_checks_result"),
    )
    op.create_index("ix_compliance_checks_order_id", "compliance_checks", ["order_id"])

    op.create_table(
        "restricted_instruments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "instrument_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instruments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "client_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("clients.id", ondelete="RESTRICT"), nullable=True
        ),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
    )
    op.create_index("ix_restricted_instruments_instrument_id", "restricted_instruments", ["instrument_id"])

    op.create_table(
        "concentration_limits",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "instrument_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("instruments.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("asset_class", sa.String(20), nullable=True),
        sa.Column("max_position_quantity", sa.Numeric(18, 4), nullable=False),
    )
    op.create_index("ix_concentration_limits_account_id", "concentration_limits", ["account_id"])

    op.create_table(
        "executions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "order_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("orders.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=False),
        sa.Column("price", sa.Numeric(18, 4), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_executions_order_id", "executions", ["order_id"])

    op.create_table(
        "settlements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("executions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("expected_settlement_date", sa.Date(), nullable=False),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fail_reason", sa.String(300), nullable=True),
        sa.CheckConstraint("status IN ('PENDING','SETTLED','FAILED')", name="ck_settlements_status"),
    )

    op.create_table(
        "cash_movements",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "account_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("accounts.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("movement_type", sa.String(30), nullable=False),
        sa.Column(
            "related_execution_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("executions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "movement_type IN ('TRADE_SETTLEMENT','DEPOSIT','WITHDRAWAL','FEE')",
            name="ck_cash_movements_movement_type",
        ),
    )
    op.create_index("ix_cash_movements_account_id", "cash_movements", ["account_id"])


def downgrade() -> None:
    op.drop_table("cash_movements")
    op.drop_table("settlements")
    op.drop_table("executions")
    op.drop_table("concentration_limits")
    op.drop_table("restricted_instruments")
    op.drop_table("compliance_checks")
    op.drop_table("order_status_history")
    op.drop_table("orders")
    op.drop_table("positions")
    op.drop_table("cash_balances")
    op.drop_table("accounts")
    op.drop_table("fx_rates")
    op.drop_table("instruments")
    op.drop_table("clients")
