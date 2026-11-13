import enum


class ClientType(str, enum.Enum):
    INDIVIDUAL = "INDIVIDUAL"
    ENTITY = "ENTITY"


class EntityStatus(str, enum.Enum):
    ACTIVE = "ACTIVE"
    CLOSED = "CLOSED"


class AccountType(str, enum.Enum):
    TAXABLE = "TAXABLE"
    RETIREMENT = "RETIREMENT"
    TRUST = "TRUST"


class AssetClass(str, enum.Enum):
    EQUITY = "EQUITY"
    BOND = "BOND"
    FUND = "FUND"
    CASH = "CASH"


class OrderSide(str, enum.Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, enum.Enum):
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, enum.Enum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class ComplianceRuleType(str, enum.Enum):
    RESTRICTED_LIST = "RESTRICTED_LIST"
    CONCENTRATION_LIMIT = "CONCENTRATION_LIMIT"
    BUYING_POWER = "BUYING_POWER"


class ComplianceResult(str, enum.Enum):
    PASS = "PASS"
    FAIL = "FAIL"


class SettlementStatus(str, enum.Enum):
    PENDING = "PENDING"
    SETTLED = "SETTLED"
    FAILED = "FAILED"


class CashMovementType(str, enum.Enum):
    TRADE_SETTLEMENT = "TRADE_SETTLEMENT"
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    FEE = "FEE"
