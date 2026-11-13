import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class CashBalanceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    currency: str
    balance: Decimal
    reserved_amount: Decimal
    updated_at: datetime


class PositionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    account_id: uuid.UUID
    instrument_id: uuid.UUID
    quantity: Decimal
    reserved_quantity: Decimal
    average_cost: Decimal
    updated_at: datetime
