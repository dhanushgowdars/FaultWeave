from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, DateTime, String, UniqueConstraint
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
