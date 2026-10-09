from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TransactionStatus(StrEnum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PaymentStatus(StrEnum):
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class LedgerEntryType(StrEnum):
    PAYMENT_COMPLETED = "PAYMENT_COMPLETED"
    TRANSACTION_COMPLETED = "TRANSACTION_COMPLETED"


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
    role: str = "customer"


class TransactionRequest(LoginRequest):
    account_number: str = Field(default="FW-DEMO-001", min_length=3, max_length=40)
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
    account_id: UUID
    amount_minor: int
    currency: str
    recipient: str
    request_id: str


class TransactionRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    account_id: UUID
    request_id: str
    amount_minor: int
    currency: str
    recipient: str
    status: TransactionStatus
    payment_id: UUID | None = None
    ledger_entry_id: UUID | None = None
    created_at: datetime
    updated_at: datetime


class TransactionComplete(BaseModel):
    payment_id: UUID
    ledger_entry_id: UUID


class PaymentCreate(BaseModel):
    transaction_id: UUID
    account_id: UUID
    amount_minor: int
    currency: str
    request_id: str


class PaymentRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    transaction_id: UUID
    account_id: UUID
    request_id: str
    amount_minor: int
    currency: str
    status: PaymentStatus
    provider_reference: str
    ledger_entry_id: UUID
    created_at: datetime


class AccountRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID
    account_number: str
    currency: str
    balance_minor: int
    is_active: bool
    created_at: datetime


class AccountLookup(BaseModel):
    account_number: str = Field(min_length=3, max_length=40)


class AccountValidation(BaseModel):
    account_id: UUID
    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)


class AccountValidationResult(BaseModel):
    account_id: UUID
    valid: bool
    reason: str | None = None


class LedgerEntryCreate(BaseModel):
    transaction_id: UUID
    payment_id: UUID | None = None
    account_id: UUID
    entry_type: LedgerEntryType
    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    request_id: str


class LedgerEntryRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    transaction_id: UUID
    payment_id: UUID | None = None
    account_id: UUID
    entry_type: LedgerEntryType
    amount_minor: int
    currency: str
    request_id: str
    created_at: datetime


class TransactionProcess(BaseModel):
    account_id: UUID
    amount_minor: int
    currency: str
    recipient: str
    request_id: str


class TransactionFlowResponse(BaseModel):
    request_id: str
    account_id: UUID
    transaction_id: UUID
    payment_id: UUID
    ledger_entry_id: UUID
    status: TransactionStatus
    message: str


class DemoAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_number: str = Field(min_length=3, max_length=40, pattern=r"^FW-[A-Z0-9-]+$")


class TransferCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_account_number: str = Field(min_length=3, max_length=40)
    destination_account_number: str = Field(min_length=3, max_length=40)
    amount_minor: int = Field(gt=0, le=100_000_000)
    currency: str = Field(default="INR", pattern=r"^[A-Z]{3}$")
    idempotency_key: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")


class TransferRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_account_id: UUID
    destination_account_id: UUID
    amount_minor: int
    currency: str
    idempotency_key: str
    status: str
    debit_entry_id: UUID
    credit_entry_id: UUID
    created_at: datetime


class TransferJournalEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    transfer_id: UUID
    account_id: UUID
    direction: str
    amount_minor: int
    currency: str
    created_at: datetime


class TransferEvidence(BaseModel):
    transfer: TransferRecord
    entries: list[TransferJournalEntry]


class TransferReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str = Field(min_length=10, max_length=500)

    @field_validator("note", mode="before")
    @classmethod
    def strip_note(cls, value):
        return value.strip() if isinstance(value, str) else value


class PaymentTransferExecution(BaseModel):
    transfer: TransferCreate
    review: TransferReview | None = None
    reviewed_by: str | None = None


class TransferPostingRequest(BaseModel):
    account_transfer_id: UUID


class TransferPostingRecord(BaseModel):
    id: UUID
    evidence: TransferEvidence
    recorded_at: datetime


class TransferTimelineEvent(BaseModel):
    """One persisted, correlated step in a durable transfer workflow."""

    model_config = ConfigDict(extra="forbid")

    event_id: str
    sequence: int = Field(ge=1)
    attempt: int = Field(ge=1)
    timestamp: datetime
    service: str
    step: str
    status: str
    detail: str
    request_id: str | None = None
    trace_id: str | None = None
    run_id: str | None = None


class TransferCaseHistory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failures: list[dict[str, Any]] = Field(default_factory=list)
    reviews: list[dict[str, Any]] = Field(default_factory=list)
    recoveries: list[dict[str, Any]] = Field(default_factory=list)


class TransferSummary(BaseModel):
    """Stable, non-nested representation used by history and review queues."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    id: str
    state: str
    source_account_number: str
    destination_account_number: str
    amount_minor: int
    currency: str
    idempotency_key: str
    created_at: datetime
    latest_event_at: datetime | None = None
    requires_review: bool
    funds_status: str
    failure_count: int = Field(ge=0)
    review_count: int = Field(ge=0)
    recovery_count: int = Field(ge=0)


class TransferPageMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    has_more: bool


class TransferCollection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    scope: Literal["customer", "engineer"]
    items: list[TransferSummary]
    page: TransferPageMetadata


class TransferSearchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    query_field: Literal["transfer_id", "request_id", "trace_id", "idempotency_key"]
    exact_match: bool = True
    items: list[TransferSummary]


class TransferEvidenceContract(BaseModel):
    """Versioned case evidence consumed by the UI and future assistant."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    transfer_id: str
    state: str
    created_at: datetime
    transfer: TransferCreate
    funds_status: str
    requires_review: bool
    timeline: list[TransferTimelineEvent]
    case_history: TransferCaseHistory
    investigation: dict[str, Any]
    journal: dict[str, Any] | None = None
    evidence_sources: dict[str, str]


class TransferEvidenceIntegrity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    algorithm: Literal["SHA-256"] = "SHA-256"
    canonical_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class TransferEvidenceExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    export_type: Literal["faultweave.transfer-evidence"] = "faultweave.transfer-evidence"
    transfer_id: str
    evidence: TransferEvidenceContract
    integrity: TransferEvidenceIntegrity
