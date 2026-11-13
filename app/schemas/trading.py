import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator

from app.enums import OrderSide, OrderType


class OrderCreate(BaseModel):
    account_id: uuid.UUID
    instrument_id: uuid.UUID
    side: OrderSide
    order_type: OrderType
    quantity: Decimal
    limit_price: Decimal | None = None
    reference_price: Decimal | None = None

    @model_validator(mode="after")
    def _check_quantity(self) -> "OrderCreate":
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        return self


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    instrument_id: uuid.UUID
    side: str
    order_type: str
    quantity: Decimal
    limit_price: Decimal | None
    reference_price: Decimal | None
    currency: str
    status: str
    filled_quantity: Decimal
    reserved_amount: Decimal
    rejection_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class ExecutionCreate(BaseModel):
    quantity: Decimal
    price: Decimal

    @model_validator(mode="after")
    def _check_positive(self) -> "ExecutionCreate":
        if self.quantity <= 0 or self.price <= 0:
            raise ValueError("quantity and price must be positive")
        return self


class ExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    quantity: Decimal
    price: Decimal
    currency: str
    executed_at: datetime


class SettlementOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    execution_id: uuid.UUID
    status: str
    expected_settlement_date: date
    settled_at: datetime | None
    fail_reason: str | None


class ComplianceCheckOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    rule_type: str
    result: str
    detail: str | None
    checked_at: datetime


class OrderStatusHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    status: str
    reason: str | None
    changed_at: datetime
