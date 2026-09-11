"""Account-scoped purchase ledger, separate from replaceable meal-plan suggestions."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from fuellayer.core.database import Base
from fuellayer.modules.onboarding.models import utc_now


class CoursesLedger(Base):
    __tablename__ = "courses_ledgers"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(default=1)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)


class CoursesRequest(Base):
    __tablename__ = "courses_requests"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    digest: Mapped[str] = mapped_column(String(64))


class ReceiptJob(Base):
    __tablename__ = "receipt_jobs"
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    image_hash: Mapped[str] = mapped_column(String(64))
    # Private, temporary payload. Cleared once analysis completes or fails.
    image: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    lease: Mapped[str | None] = mapped_column(String(36))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
