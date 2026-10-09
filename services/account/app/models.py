from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "account_records"
    __table_args__ = (
        UniqueConstraint("user_id", "account_number", name="uq_account_owner_number"),
        {"schema": "accounts"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    account_number: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    balance_minor: Mapped[int] = mapped_column(BigInteger, default=100_000_000)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_transfer_user_key"),
        CheckConstraint("amount_minor > 0", name="ck_transfer_positive_amount"),
        CheckConstraint(
            "source_account_id <> destination_account_id", name="ck_transfer_distinct_accounts"
        ),
        {"schema": "accounts"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    source_account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.account_records.id")
    )
    destination_account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.account_records.id")
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="COMPLETED")
    debit_entry_id: Mapped[str] = mapped_column(String(36))
    credit_entry_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class TransferEntry(Base):
    __tablename__ = "transfer_entries"
    __table_args__ = (
        UniqueConstraint("transfer_id", "direction", name="uq_transfer_entry_direction"),
        CheckConstraint("amount_minor > 0", name="ck_entry_positive_amount"),
        CheckConstraint("direction IN ('DEBIT', 'CREDIT')", name="ck_entry_direction"),
        {"schema": "accounts"},
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    transfer_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.transfers.id"), index=True
    )
    account_id: Mapped[str] = mapped_column(String(36), index=True)
    direction: Mapped[str] = mapped_column(String(6))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
