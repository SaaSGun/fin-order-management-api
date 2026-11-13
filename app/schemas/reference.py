import uuid
from decimal import Decimal

from pydantic import BaseModel, ConfigDict


class ClientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    client_type: str
    risk_profile: str
    status: str


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    client_id: uuid.UUID
    account_name: str
    base_currency: str
    account_type: str
    status: str


class InstrumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol: str
    name: str
    asset_class: str
    currency: str
    status: str


class AccountSummaryOut(BaseModel):
    account_id: uuid.UUID
    base_currency: str
    total_cash: Decimal
    total_positions_at_cost: Decimal
    total_value: Decimal
