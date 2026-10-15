"""CI schedule and notification persistence; schedules advance by claim, never overlap."""

from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ScheduleModel(Base):
    __tablename__ = "ci_schedules"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    watchlist_id: Mapped[str] = mapped_column(ForeignKey("ci_watchlists.id", ondelete="RESTRICT"), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    cadence: Mapped[str] = mapped_column(String(16), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    run_at: Mapped[str] = mapped_column(String(5), nullable=False, default="08:00")
    day_of_week: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    next_due_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (CheckConstraint("cadence IN ('daily','weekly')", name="ck_ci_schedule_cadence"),
                      Index("ix_ci_schedules_due", "active", "next_due_at"))


class NotificationModel(Base):
    __tablename__ = "ci_notifications"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(36), nullable=False)
    watchlist_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(String(2000), nullable=False, default="")
    read_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("kind IN ('run_completed','run_failed','brief_ready','schedule_skipped',"
                        "'schedule_blocked','schedule_fired')", name="ck_ci_notification_kind"),
        Index("ix_ci_notifications_owner", "owner_id", "read_at", "created_at"))
