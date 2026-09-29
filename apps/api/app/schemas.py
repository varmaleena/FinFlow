from typing import Literal
from pydantic import BaseModel, Field

class Conversation(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    transaction_id: str | None = None
    language: Literal['en-IN', 'hi-IN'] = 'en-IN'

class TransactionInput(BaseModel):
    transaction_id: str

class SettlementInput(BaseModel):
    settlement_id: str

class CreditInput(BaseModel):
    principal: float = Field(default=50000, ge=1000, le=1000000, allow_inf_nan=False)
    months: int = Field(default=12, ge=1, le=60)
    annual_rate: float = Field(default=18, ge=0, le=48, allow_inf_nan=False)

class EvaluateInput(BaseModel):
    case_id: str
    action_type: str

class ExecuteInput(BaseModel):
    policy_decision_id: str
    idempotency_key: str = Field(min_length=8, max_length=160)
