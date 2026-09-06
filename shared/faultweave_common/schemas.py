from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TransactionStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PaymentStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class HealthResponse(BaseModel):
    service: str
    status: str
    database: str | None = None


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = 900
    user_id: UUID


class TransactionRequest(LoginRequest):
    amount_minor: int = Field(gt=0, le=100_000_000)
    currency: str = Field(default="INR", min_length=3, max_length=3)
    recipient: str = Field(min_length=1, max_length=120)

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        if not value.isalpha():
            raise ValueError("currency must contain three letters")
        return value.upper()


class TransactionCreate(BaseModel):
    amount_minor: int
    currency: str
    recipient: str
    request_id: str


class TransactionRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    request_id: str
    amount_minor: int
    currency: str
    recipient: str
    status: TransactionStatus
    payment_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class TransactionComplete(BaseModel):
    payment_id: UUID


class PaymentCreate(BaseModel):
    transaction_id: UUID
    amount_minor: int
    currency: str
    request_id: str


class PaymentRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    transaction_id: UUID
    request_id: str
    amount_minor: int
    currency: str
    status: PaymentStatus
    provider_reference: str
    created_at: datetime


class TransactionFlowResponse(BaseModel):
    request_id: str
    transaction_id: UUID
    payment_id: UUID
    status: TransactionStatus
    message: str
