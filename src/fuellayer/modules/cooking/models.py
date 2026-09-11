"""Account-owned cooking snapshots; timers/progress share one atomic session revision."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from fuellayer.core.database import Base
from fuellayer.modules.onboarding.models import utc_now


class CookingSession(Base):
    __tablename__ = "cooking_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress','awaiting_confirmation','completed','abandoned')"
        ),
        Index(
            "one_unfinished_cooking_session",
            "user_id",
            unique=True,
            postgresql_where=text("status IN ('in_progress','awaiting_confirmation')"),
            sqlite_where=text("status IN ('in_progress','awaiting_confirmation')"),
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(24), default="in_progress")
    version: Mapped[int] = mapped_column(default=1)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    state: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CookingCommand(Base):
    __tablename__ = "cooking_commands"
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cooking_sessions.id", ondelete="CASCADE")
    )
    digest: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSON)


class CookingConsumption(Base):
    __tablename__ = "cooking_consumptions"
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("cooking_sessions.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(12))
    # Exact before/after revisions survive later lot edits/removal. No cascading lot FK.
    lots: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    uses: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    undone_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
