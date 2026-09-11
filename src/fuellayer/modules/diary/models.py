"""Account-scoped diary snapshots, revisions, tombstones and retry receipts."""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fuellayer.core.database import Base
from fuellayer.modules.onboarding.models import utc_now


class DiaryRecord(Base):
    __tablename__ = "diary_entries"
    __table_args__ = (UniqueConstraint("user_id", "entry_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    entry_id: Mapped[str] = mapped_column(String(160))
    diary_date: Mapped[date] = mapped_column(Date, index=True)
    timezone: Mapped[str | None] = mapped_column(String(100), nullable=True)
    offset_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer)
    change_seq: Mapped[int] = mapped_column(Integer, index=True)
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class DiaryRevision(Base):
    __tablename__ = "diary_revisions"
    __table_args__ = (UniqueConstraint("user_id", "entry_id", "revision"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    entry_id: Mapped[str] = mapped_column(String(160))
    revision: Mapped[int] = mapped_column(Integer)
    record: Mapped[dict[str, Any]] = mapped_column(JSON)


class DiaryReceipt(Base):
    __tablename__ = "diary_receipts"
    __table_args__ = (UniqueConstraint("user_id", "request_id"),)
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    request_id: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict[str, Any]] = mapped_column(JSON)


class DiaryDay(Base):
    """First recorded calendar context; never recomputed when the user travels."""

    __tablename__ = "diary_days"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    diary_date: Mapped[date] = mapped_column(Date, primary_key=True)
    timezone: Mapped[str | None] = mapped_column(String(100), nullable=True)
